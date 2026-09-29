"""Phase 4 — train the GRU4Rec sequence model (ADR 0004: this is the main
ranking model, not an optional extension). Architecture per
`docs/bao-cao-ky-thuat.md` mục 7.6: Embedding -> GRU (1 layer) -> Linear +
sampled softmax, plain PyTorch, no training framework.

The "Linear" projects the GRU's final hidden state into the *embedding*
space rather than into a full `vocab_size`-wide logit space — logits for a
training step are then a dot product against only the true target's row and
`--num-negatives` sampled rows of the same embedding table (weight tying
between input and output). This is what makes it a *sampled* softmax: the
full vocabulary is never matrix-multiplied against on every step, only a
small sampled subset, which is the entire performance point of the
technique (see `bao-cao-ky-thuat.md` mục 7.6's own parenthetical about this
being the most expensive part if skipped).

Negative sampling is uniform over the vocabulary, shared across one whole
batch (not resampled per example) — simple, cheap, and easy to explain at
this data scale; not split into a separate `negative_sampling.py` file
(ADR 0004 dropped that file from Phase 4's scope along with LightGBM).
"""

from __future__ import annotations

import argparse
import json

import mlflow
import mlflow.pytorch
import torch
from pyiceberg.expressions import EqualTo
from pyiceberg.table import Table
from torch import nn
from torch.utils.data import DataLoader, Dataset

from jobs.training.build_sequence_dataset import PAD_INDEX, Example, load_dataset
from jobs.training.evaluate import evaluate_val_set
from reco_mlops_libs.common.env import require_env
from reco_mlops_libs.iceberg.catalog import get_catalog
from reco_mlops_libs.iceberg.schema_contracts import gold_user_features
from reco_mlops_libs.mlflow_utils.logging_helpers import log_iceberg_snapshot
from reco_mlops_libs.ranking.base import RankingModel
from reco_mlops_libs.ranking.gru4rec import GRU4RecNet

EXPERIMENT_NAME = "reco-mlops-gru4rec"
REGISTERED_MODEL_NAME = "reco-mlops-gru4rec"
DEVICE = torch.device("cpu")  # CPU-only by design, see bao-cao-ky-thuat.md 7.6


class SequenceExampleDataset(Dataset):
    def __init__(self, examples: tuple[Example, ...]) -> None:
        self.examples = examples

    def __len__(self) -> int:
        return len(self.examples)

    def __getitem__(self, index: int) -> tuple[list[int], int]:
        example = self.examples[index]
        return list(example.input_indices), example.target_index


def collate_batch(
    batch: list[tuple[list[int], int]],
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    inputs, targets = zip(*batch)
    lengths = torch.tensor([len(seq) for seq in inputs], dtype=torch.long)
    max_len = int(lengths.max())
    padded = torch.full((len(inputs), max_len), PAD_INDEX, dtype=torch.long)
    for row, seq in enumerate(inputs):
        padded[row, : len(seq)] = torch.tensor(seq, dtype=torch.long)
    return padded, lengths, torch.tensor(targets, dtype=torch.long)


def train_one_epoch(
    net: GRU4RecNet,
    loader: DataLoader,
    optimizer: torch.optim.Optimizer,
    vocab_size: int,
    num_negatives: int,
) -> float:
    net.train()
    total_loss = 0.0
    num_batches = 0
    for inputs, lengths, targets in loader:
        inputs, lengths, targets = inputs.to(DEVICE), lengths.to(DEVICE), targets.to(DEVICE)
        optimizer.zero_grad()

        projected = net.encode(inputs, lengths)  # (B, D)
        target_logits = (projected * net.item_embedding(targets)).sum(dim=1, keepdim=True)

        # Shared negatives for the whole batch — uniform over the
        # vocabulary, excluding PAD_INDEX (0).
        negative_indices = torch.randint(1, vocab_size, (num_negatives,), device=DEVICE)
        negative_logits = projected @ net.item_embedding(negative_indices).T  # (B, K)

        logits = torch.cat([target_logits, negative_logits], dim=1)  # (B, 1+K)
        labels = torch.zeros(logits.size(0), dtype=torch.long, device=DEVICE)
        loss = nn.functional.cross_entropy(logits, labels)

        loss.backward()
        optimizer.step()
        total_loss += loss.item()
        num_batches += 1
    return total_loss / max(num_batches, 1)


class GRU4RecRanker(RankingModel):
    """Wraps a trained `GRU4RecNet` + item vocab to implement
    `RankingModel.predict()`. Used here only as a self-check that the
    interface holds before logging the model — Phase 5's `model_loader.py`
    decides independently how to reconstruct one of these from the MLflow
    artifact.
    """

    def __init__(self, net: GRU4RecNet, vocab: dict[str, int], user_features_table: Table) -> None:
        self.net = net
        self.vocab = vocab
        self.user_features_table = user_features_table

    def _user_sequence_indices(self, user_id: str) -> list[int] | None:
        arrow = self.user_features_table.scan(
            row_filter=EqualTo("user_id", user_id), selected_fields=("item_sequence",)
        ).to_arrow()
        if arrow.num_rows == 0:
            return None
        product_ids = arrow.column("item_sequence").to_pylist()[0]
        indices = [self.vocab[pid] for pid in product_ids if pid in self.vocab]
        return indices or None

    def predict(self, user_id: str, candidate_items: list[str]) -> list[float]:
        indices = self._user_sequence_indices(user_id)
        if indices is None:
            # Cold-start user (no gold.user_features row, or nothing in
            # vocab): neutral score for every candidate — real fallback
            # policy (e.g. popularity-based) is Phase 5's job.
            return [0.0] * len(candidate_items)

        self.net.eval()
        with torch.no_grad():
            input_tensor = torch.tensor([indices], dtype=torch.long)
            lengths = torch.tensor([len(indices)], dtype=torch.long)
            projected = self.net.encode(input_tensor, lengths)  # (1, D)
            scores: list[float] = []
            for item in candidate_items:
                candidate_index = self.vocab.get(item)
                if candidate_index is None:
                    scores.append(0.0)
                    continue
                embedding = self.net.item_embedding(torch.tensor([candidate_index]))
                scores.append((projected @ embedding.T).item())
        return scores


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train the Phase 4 GRU4Rec sequence model.")
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--embedding-dim", type=int, default=32)
    parser.add_argument("--hidden-size", type=int, default=64)
    parser.add_argument("--num-negatives", type=int, default=100)
    return parser.parse_args()


def run_training(
    dataset, args: argparse.Namespace, *, user_features_table: Table | None = None, register: bool = True
) -> dict:
    """Core training routine — no Iceberg/MLflow-tracking-URI setup of its
    own, so it can run against any `SequenceDataset` (Gold via
    `load_dataset()` for real runs, a small fixed fixture for
    `tests/model/test_training_smoke.py`) under whatever `mlflow.set_tracking_uri()`
    the caller already configured (e.g. `file:///tmp/mlruns` in CI, no
    tracking server needed). `main()` below is the thin CLI wrapper that
    wires up the real data source and calls this.

    `user_features_table=None` skips the `GRU4RecRanker` self-check (needs a
    real Iceberg table to query `user_id` against) and `register=False`
    skips `mlflow.register_model()` (the smoke test's experiment name isn't
    meant to pollute the real `reco-mlops-gru4rec` registry) — both stay on
    by default so `main()`'s behavior is unchanged from before this was
    split out.
    """
    vocab_size = len(dataset.vocab) + 1  # +1 for PAD_INDEX

    net = GRU4RecNet(vocab_size, args.embedding_dim, args.hidden_size).to(DEVICE)
    optimizer = torch.optim.Adam(net.parameters(), lr=args.lr)
    train_loader = DataLoader(
        SequenceExampleDataset(dataset.train_examples),
        batch_size=args.batch_size,
        shuffle=True,
        collate_fn=collate_batch,
    )

    mlflow.set_experiment(EXPERIMENT_NAME)

    with mlflow.start_run() as run:
        mlflow.set_tag("model_type", "sequence")
        mlflow.log_params({
            "epochs": args.epochs,
            "batch_size": args.batch_size,
            "lr": args.lr,
            "embedding_dim": args.embedding_dim,
            "hidden_size": args.hidden_size,
            "num_negatives": args.num_negatives,
            "vocab_size": vocab_size,
            "train_examples": len(dataset.train_examples),
            "val_examples": len(dataset.val_examples),
        })
        if user_features_table is not None:
            log_iceberg_snapshot(user_features_table, gold_user_features.TABLE_IDENTIFIER)

        for epoch in range(1, args.epochs + 1):
            train_loss = train_one_epoch(
                net, train_loader, optimizer, vocab_size, args.num_negatives
            )
            mlflow.log_metric("train_loss", train_loss, step=epoch)
            print(json.dumps({"epoch": epoch, "train_loss": train_loss}))

        metrics = evaluate_val_set(net, dataset.val_examples, k=10)
        mlflow.log_metrics(metrics)

        mlflow.log_dict(dataset.vocab, "item_vocab.json")
        model_info = mlflow.pytorch.log_model(net, name="model")

        result = {"run_id": run.info.run_id, **metrics}

        if register:
            registered = mlflow.register_model(model_info.model_uri, REGISTERED_MODEL_NAME)
            result["registered_model"] = REGISTERED_MODEL_NAME
            result["registered_version"] = registered.version

        if user_features_table is not None:
            # Self-check: the interface must hold before anything is
            # considered done — pick one user with a sequence and confirm
            # predict() runs.
            ranker = GRU4RecRanker(net, dataset.vocab, user_features_table)
            sample_user = user_features_table.scan(selected_fields=("user_id",)).to_arrow()
            if sample_user.num_rows > 0:
                sample_user_id = sample_user.column("user_id")[0].as_py()
                sample_candidates = list(dataset.vocab)[:5]
                sample_scores = ranker.predict(sample_user_id, sample_candidates)
                assert len(sample_scores) == len(sample_candidates)

        return result


def main() -> int:
    args = parse_args()
    dataset = load_dataset()

    mlflow.set_tracking_uri(require_env("MLFLOW_TRACKING_URI"))

    catalog = get_catalog()
    user_features_table = catalog.load_table(gold_user_features.TABLE_IDENTIFIER)

    result = run_training(dataset, args, user_features_table=user_features_table)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
