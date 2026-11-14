"""Phase 10 (ADR 0008) — popularity baseline on the exact same split and
ranking protocol as `reco_mlops_libs.ranking.agf.trainer.evaluate` (full
ranking over every item, only `[PAD]` masked, NDCG/Recall @10/@20), so the
number logged next to AGF's in MLflow is directly comparable. Pure numpy.

Popularity = number of times each item appears in the TRAIN part
(`train_counts`, index 0 = `[PAD]`) — never valid/test, same rule as the
experiment's `Pop`. Ties are broken by item index (deterministic).
"""

from __future__ import annotations

import numpy as np

TOPK = (10, 20)


def popularity_ranks(train_counts: np.ndarray) -> np.ndarray:
    """1-based rank of every item index under popularity; `[PAD]` last."""
    counts = np.asarray(train_counts, dtype=float).copy()
    counts[0] = -np.inf
    n = len(counts)
    order = np.lexsort((np.arange(n), -counts))  # most popular first, ties by index
    ranks = np.empty(n, dtype=np.int64)
    ranks[order] = np.arange(1, n + 1)
    return ranks


def popularity_metrics(targets: np.ndarray, train_counts: np.ndarray) -> dict[str, float]:
    """Same keys as `trainer.evaluate`: `ndcg@10`, `recall@10`, ..."""
    targets = np.asarray(targets, dtype=np.int64)
    if len(targets) == 0:
        return {f"{m}@{k}": 0.0 for k in TOPK for m in ("recall", "ndcg")}
    r = popularity_ranks(train_counts)[targets].astype(float)
    out = {}
    for k in TOPK:
        hit = r <= k
        out[f"recall@{k}"] = float(hit.mean())
        out[f"ndcg@{k}"] = float(np.where(hit, 1.0 / np.log2(r + 1.0), 0.0).mean())
    return out
