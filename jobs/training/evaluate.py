"""Phase 4 — ranking metrics: NDCG, Recall, MAP, Coverage @K.

The 4 `*_at_k` functions are pure (no torch/mlflow import needed to use
them directly) so they're unit-testable without any infra — see
`tests/unit/training/test_evaluate.py`. `evaluate_val_set()` is the only
function that knows about a trained `GRU4RecNet`, called by
`train_sequence.py` right after training and by this module's own `main()`
to re-evaluate an already-registered run on demand.
"""

from __future__ import annotations

import argparse
import json
import math

import mlflow
import mlflow.pytorch
import torch

from jobs.training.build_sequence_dataset import Example, load_dataset
from reco_mlops_libs.common.env import require_env

DEFAULT_K = 10


def _rank_of_target(scores: list[float], target_index: int) -> int:
    """1-based rank of `target_index` among all scored items (rank 1 = the
    single highest score)."""
    target_score = scores[target_index]
    return 1 + sum(1 for score in scores if score > target_score)


def ndcg_at_k(scores: list[float], target_index: int, k: int = DEFAULT_K) -> float:
    rank = _rank_of_target(scores, target_index)
    return 1.0 / math.log2(rank + 1) if rank <= k else 0.0


def recall_at_k(scores: list[float], target_index: int, k: int = DEFAULT_K) -> float:
    return 1.0 if _rank_of_target(scores, target_index) <= k else 0.0


def map_at_k(scores: list[float], target_index: int, k: int = DEFAULT_K) -> float:
    """Single relevant item per example, so AP@k collapses to 1/rank (if
    within the top k) — matches `recall_at_k`'s hit condition."""
    rank = _rank_of_target(scores, target_index)
    return 1.0 / rank if rank <= k else 0.0


def coverage_at_k(
    top_k_indices_per_example: list[list[int]], vocab_size: int, k: int = DEFAULT_K
) -> float:
    """Fraction of the catalog that appears in *some* example's top-k."""
    recommended = {index for top_k in top_k_indices_per_example for index in top_k}
    return len(recommended) / vocab_size if vocab_size else 0.0


def evaluate_val_set(net, val_examples: tuple[Example, ...], k: int = DEFAULT_K) -> dict[str, float]:
    """Score the full vocabulary for every validation example and compute
    NDCG/Recall/MAP/Coverage @k. Cheap at this data scale (~1.5k items,
    ~1.5k users) — no need to restrict to a candidate subset."""
    net.eval()
    ndcg_values: list[float] = []
    recall_values: list[float] = []
    map_values: list[float] = []
    top_k_indices_per_example: list[list[int]] = []
    vocab_size = net.item_embedding.num_embeddings

    with torch.no_grad():
        for example in val_examples:
            input_tensor = torch.tensor([list(example.input_indices)], dtype=torch.long)
            lengths = torch.tensor([len(example.input_indices)], dtype=torch.long)
            scores = net.score_all(input_tensor, lengths)[0].tolist()

            ndcg_values.append(ndcg_at_k(scores, example.target_index, k))
            recall_values.append(recall_at_k(scores, example.target_index, k))
            map_values.append(map_at_k(scores, example.target_index, k))
            top_k = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:k]
            top_k_indices_per_example.append(top_k)

    count = max(len(val_examples), 1)
    return {
        f"val_ndcg_at_{k}": sum(ndcg_values) / count,
        f"val_recall_at_{k}": sum(recall_values) / count,
        f"val_map_at_{k}": sum(map_values) / count,
        f"val_coverage_at_{k}": coverage_at_k(top_k_indices_per_example, vocab_size, k),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Re-evaluate an already-trained/registered Phase 4 run on the current val split."
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--k", type=int, default=DEFAULT_K)
    args = parser.parse_args()

    mlflow.set_tracking_uri(require_env("MLFLOW_TRACKING_URI"))
    model_uri = f"runs:/{args.run_id}/model"
    net = mlflow.pytorch.load_model(model_uri)

    dataset = load_dataset()
    metrics = evaluate_val_set(net, dataset.val_examples, k=args.k)
    print(json.dumps({"run_id": args.run_id, **metrics}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
