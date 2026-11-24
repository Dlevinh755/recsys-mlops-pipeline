"""ADR 0013 — score the current `production` model (the champion) on the
**same validation/test samples the challenger was just trained and evaluated
on**, so `promote.py` compares two numbers measured on one split.

Why: the two runs' own `val_ndcg_at_10` come from different splits whenever the
data changed between them (new users/items → different valid set), so the raw
numbers are not comparable (measured 2026-11-24: v5 0.0847 on 1,517 samples vs
v4 0.0920 on 1,378; the popularity baseline fell 0.0442 → 0.0373 too).

How the champion is scored — the same way `serving` scores it
(`serving/app/core/model_loader.py::score_candidates`), not through the
trainer's own `evaluate` (that one needs the model's item attributes):

    state_u   = net.seq_output(seq, len, item_table)       # champion artifacts
    score(i)  = item_table[i] · state_u   for items the champion knows
              = 0.0                       for items it does not (serving's rule)

Vocabularies differ between champion and challenger (the catalog grows), so
samples (indexed in the challenger's vocab) are translated through
`product_id`:

* a history item the champion does not know is dropped from the sequence;
* a sample whose **target** the champion does not know counts as a miss (the
  champion cannot recommend an item it has never seen — real production
  behaviour; it makes the champion look slightly worse when many new items
  appear, so the counts are logged next to the metrics);
* a sample whose history is empty after dropping counts as a miss.

Ranks use the trainer's own `full_ranks` (full ranking over the challenger's
catalog, only `[PAD]` masked, top-k with `k = 20`), so the protocol is the
challenger's. Pure torch/numpy here; the MLflow loading is at the bottom.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np
import torch

from reco_mlops_libs.ranking.agf.trainer import TOPK, Samples, full_ranks

ITEM_TABLE_ARTIFACT = "agf/item_table.npy"
VOCAB_ARTIFACT = "item_vocab.json"
SPLITS = ("valid", "test")
_SPLIT_PREFIX = {"valid": "val", "test": "test"}


@dataclass(frozen=True)
class Champion:
    version: str
    net: torch.nn.Module  # AGFSequentialModel, eval mode
    vocab: dict[str, int]  # product_id -> row of item_table (1-based, 0 = [PAD])
    item_table: torch.Tensor  # (len(vocab) + 1, d)


def ranks_to_metrics(ranks: np.ndarray) -> dict[str, float]:
    """Same keys/formula as `trainer.evaluate` (`ndcg@10`, `recall@10`, ...)."""
    r = np.asarray(ranks, dtype=float)
    out = {}
    for k in TOPK:
        hit = r <= k
        out[f"recall@{k}"] = float(hit.mean()) if len(r) else 0.0
        out[f"ndcg@{k}"] = float(np.where(hit, 1.0 / np.log2(r + 1.0), 0.0).mean()) if len(r) else 0.0
    return out


@torch.no_grad()
def champion_ranks(
    champion: Champion, samples: Samples, challenger_tokens: list[str], batch_size: int = 2048
) -> tuple[np.ndarray, dict[str, int]]:
    """Rank (1-based, `max(TOPK) + 1` = miss) of every sample's target under
    the champion, plus counts of what could not be translated.

    `challenger_tokens[i]` is the product id at index `i` of the challenger's
    vocabulary (index 0 = `[PAD]`), i.e. `CatalogItems.tokens`."""
    miss = max(TOPK) + 1
    net, table = champion.net.eval(), champion.item_table
    to_champ = np.array(
        [0 if i == 0 else champion.vocab.get(token, -1) for i, token in enumerate(challenger_tokens)],
        dtype=np.int64,
    )
    n, width = len(samples), int(getattr(net, "max_len", samples.seq.shape[1]))

    seq = np.zeros((n, width), dtype=np.int64)
    lengths = np.zeros(n, dtype=np.int64)
    dropped = 0
    for row in range(n):
        mapped = to_champ[samples.seq[row, : samples.seq_len[row]]]
        kept = mapped[mapped > 0]
        dropped += len(mapped) - len(kept)
        kept = kept[-width:]
        seq[row, : len(kept)] = kept
        lengths[row] = len(kept)
    target_known = to_champ[samples.target] > 0
    scorable = (lengths > 0) & target_known

    known_columns = np.flatnonzero(to_champ > 0)  # challenger columns the champion knows
    known_rows = torch.from_numpy(to_champ[known_columns])
    known_table = table[known_rows]  # (n_known, d)
    n_columns = len(challenger_tokens)

    ranks = np.full(n, miss, dtype=np.int64)
    idx = np.flatnonzero(scorable)
    for start in range(0, len(idx), batch_size):
        part = idx[start : start + batch_size]
        state = net.seq_output(torch.from_numpy(seq[part]), torch.from_numpy(lengths[part]), table)
        scores = torch.zeros(len(part), n_columns)
        scores[:, known_columns] = state @ known_table.T
        scores[:, 0] = -np.inf  # only [PAD] is masked, as in trainer.evaluate
        ranks[part] = full_ranks(scores, torch.from_numpy(samples.target[part])).numpy()

    info = {
        "samples": n,
        "unknown_target": int((~target_known).sum()),
        "no_history": int((lengths == 0).sum()),
        "dropped_history_items": int(dropped),
    }
    return ranks, info


def evaluate_champion(
    champion: Champion, samples: dict[str, Samples], challenger_tokens: list[str]
) -> tuple[dict[str, float], dict[str, object]]:
    """`(metrics, params)` to log on the challenger's run.

    metrics: `champion_{val,test}_{ndcg,recall}_at_{10,20}_same_split`.
    params: `champion_version` and, per split, how many samples the champion
    could not be scored on (`champion_{val,test}_unknown_target` / `_no_history`)."""
    metrics: dict[str, float] = {}
    params: dict[str, object] = {"champion_version": champion.version}
    for split in SPLITS:
        ranks, info = champion_ranks(champion, samples[split], challenger_tokens)
        prefix = _SPLIT_PREFIX[split]
        for key, value in ranks_to_metrics(ranks).items():
            metrics[f"champion_{prefix}_{key.replace('@', '_at_')}_same_split"] = value
        params[f"champion_{prefix}_unknown_target"] = info["unknown_target"]
        params[f"champion_{prefix}_no_history"] = info["no_history"]
    return metrics, params


# ------------------------------------------------------------------ MLflow


def load_champion(model_uri: str, run_id: str, version: str, client=None) -> Champion:
    """Load a registered model (or a bare run's model) with the artifacts
    serving reads: `item_vocab.json` and `agf/item_table.npy` of the same run."""
    import mlflow.pytorch

    from reco_mlops_libs.mlflow_utils.registry_client import get_client

    client = client or get_client()
    net = mlflow.pytorch.load_model(model_uri)
    net.eval()
    with open(client.download_artifacts(run_id, VOCAB_ARTIFACT), encoding="utf-8") as file:
        vocab: dict[str, int] = json.load(file)
    table = torch.from_numpy(np.load(client.download_artifacts(run_id, ITEM_TABLE_ARTIFACT))).float()
    if table.shape[0] != len(vocab) + 1:
        raise ValueError(f"champion item_table has {table.shape[0]} rows, expected {len(vocab) + 1}")
    return Champion(version=str(version), net=net, vocab=vocab, item_table=table)


def load_production_champion(model_name: str, client=None) -> Champion | None:
    """The model behind the `production` alias, or `None` if there is none yet."""
    from reco_mlops_libs.mlflow_utils.registry_client import get_client, get_production_version

    client = client or get_client()
    production = get_production_version(model_name, client=client)
    if production is None:
        return None
    return load_champion(f"models:/{model_name}/{production.version}", production.run_id,
                         production.version, client=client)
