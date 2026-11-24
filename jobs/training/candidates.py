"""ADR 0015 — several AGF candidates per training run, pick one.

`train_agf --candidates gru4rec,sasrec,gru4rec_sum` trains each candidate on the
**same split** (the split depends only on the interactions and the seed, not on
the backbone), then this module picks the winner **on validation**; test numbers
are only reported. Candidates differ along the axes `MethodConfig` allows
(backbone, how attributes are fused) — all inside the AGF family. Models outside
it (FDSA, SASRecF, BERT4Rec, BPR...) only exist as RecBole baselines in the
experiment repo and cannot be loaded through `MethodConfig`: out of scope.

Selection rule (the one ADR 0008 used to choose between GRU4Rec and SASRec:
prefer the simpler model unless the gain is clear):

* the default candidate is `gru4rec` (the model chosen in Chapter 3), or the
  first candidate when `gru4rec` is not in the list;
* another candidate is eligible only if its mean validation NDCG@10 is higher
  than the default's **and** the paired per-sample difference exceeds one
  standard error of that difference;
* among eligible candidates take the highest `val_ndcg_at_10`; ties go to the
  one with fewer parameters, then to list order; nobody eligible → the default.

Winner's curse: choosing 1 of K on ~1.5K validation samples is still mildly
optimistic, and the K-1 comparisons against the default are not corrected for
multiplicity. The numbers say "chosen by this rule", not "significantly better".
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import torch

from reco_mlops_libs.ranking.agf.trainer import Samples, full_ranks

DEFAULT_ID = "gru4rec"
DEFAULT_CANDIDATES = (DEFAULT_ID,)
SELECTION_RULE = "paired_1se_vs_default"

# id -> MethodConfig overrides on top of `agf_method_config.yaml`. Everything not
# listed (d, max_len, dropout, output_fusion, popularity, text, lr...) is shared.
CANDIDATE_OVERRIDES: dict[str, dict] = {
    "gru4rec": {"backbone": "gru4rec", "fusion": "gate"},  # chosen in Chapter 3
    "sasrec": {"backbone": "sasrec", "fusion": "gate"},  # attention instead of GRU
    "gru4rec_sum": {"backbone": "gru4rec", "fusion": "sum"},  # no gate: h = e + f
    "gru4rec_idonly": {"backbone": "gru4rec", "fusion": "id_only"},  # control: attributes off
}


def parse_candidates(value: str | None) -> list[str]:
    """`"gru4rec, sasrec"` → `["gru4rec", "sasrec"]`; empty → the default. Unknown ids and
    duplicates are errors (a typo must not silently train something else)."""
    ids = [part.strip() for part in (value or "").split(",") if part.strip()] or list(DEFAULT_CANDIDATES)
    unknown = [i for i in ids if i not in CANDIDATE_OVERRIDES]
    if unknown:
        raise ValueError(f"unknown candidate(s) {unknown}; known: {sorted(CANDIDATE_OVERRIDES)}")
    if len(set(ids)) != len(ids):
        raise ValueError(f"duplicate candidate in {ids}")
    return ids


@torch.no_grad()
def ndcg_per_sample(model, samples: Samples, k: int = 10, batch_size: int = 2048,
                    device: str | torch.device = "cpu") -> np.ndarray:
    """NDCG@k of every sample, same ranking protocol as `trainer.evaluate`
    (full ranking, only `[PAD]` masked) — its mean equals that function's `ndcg@k`."""
    model.eval()
    h_in, h_out = model.item_tables()
    out = []
    for start in range(0, len(samples), batch_size):
        stop = min(start + batch_size, len(samples))
        seq = torch.as_tensor(samples.seq[start:stop], device=device)
        lengths = torch.as_tensor(samples.seq_len[start:stop], device=device)
        target = torch.as_tensor(samples.target[start:stop], device=device)
        scores = model.score_with_tables(seq, lengths, h_in, h_out)
        scores[:, 0] = -np.inf
        ranks = full_ranks(scores, target).cpu().numpy().astype(float)
        out.append(np.where(ranks <= k, 1.0 / np.log2(ranks + 1.0), 0.0))
    return np.concatenate(out) if out else np.zeros(0)


@dataclass(frozen=True)
class Scored:
    id: str
    val_ndcg: float
    per_sample: np.ndarray  # validation NDCG@10 of each sample, same order for every candidate
    n_params: int


@dataclass(frozen=True)
class Selection:
    winner: str
    default: str
    rule: str
    margins: dict[str, dict]  # non-default id -> {diff, se, z, eligible}


def select_winner(scored: list[Scored], z: float = 1.0) -> Selection:
    """Pure (no I/O). See the module docstring for the rule."""
    if not scored:
        raise ValueError("no candidate to choose from")
    default = next((c for c in scored if c.id == DEFAULT_ID), scored[0])
    margins: dict[str, dict] = {}
    for candidate in scored:
        if candidate is default:
            continue
        if len(candidate.per_sample) != len(default.per_sample):
            raise ValueError("candidates were not scored on the same validation samples")
        diff = candidate.per_sample - default.per_sample
        n = len(diff)
        se = float(diff.std(ddof=1) / np.sqrt(n)) if n > 1 else 0.0
        mean = float(diff.mean()) if n else 0.0
        eligible = mean > 0 and mean > z * se
        margins[candidate.id] = {
            "diff": mean, "se": se, "z": (mean / se) if se > 0 else None, "eligible": eligible,
        }
    order = {c.id: i for i, c in enumerate(scored)}
    # Only challengers that cleared the bar compete, on raw score; else the default stays.
    eligible = [c for c in scored if c is not default and margins[c.id]["eligible"]]
    winner = default
    if eligible:
        winner = min(eligible, key=lambda c: (-c.val_ndcg, c.n_params, order[c.id]))
    return Selection(winner=winner.id, default=default.id, rule=SELECTION_RULE, margins=margins)
