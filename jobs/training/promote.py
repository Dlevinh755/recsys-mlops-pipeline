"""Phase 4 — promotion gate: mark a newly trained model version as
`production` only if it beats the current champion (challenger/champion
pattern), never by an arbitrary hard-coded quality floor. The very first
version ever trained for a model name is always promoted (there is no
champion yet to compare against).

Comparison rule (ADR 0013): the challenger's run carries the champion's score
**on the challenger's own validation split** (`champion_val_ndcg_at_10_same_split`,
written by `train_agf` — `jobs/training/champion_eval.py`). Comparing the two
runs' own `val_ndcg_at_10` is wrong whenever the data changed between them
(different users/items → different valid set), so that number is only the
`legacy` fallback for runs that have no same-split score.

Default model name is the shared `REGISTERED_MODEL_NAME` (AGF-GRU4Rec,
ADR 0008). The legacy ID-only GRU4Rec can still be promoted by hand with
`--model-name reco-mlops-gru4rec` — the two registries are never compared
against each other (different split/metric protocol, see ADR 0008).
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass

from mlflow import MlflowClient

from reco_mlops_libs.mlflow_utils.registry_client import (
    get_client,
    get_production_version,
    set_production_version,
)
from reco_mlops_libs.ranking.base import REGISTERED_MODEL_NAME

METRIC_NAME = "val_ndcg_at_10"
SAME_SPLIT_METRIC = "champion_val_ndcg_at_10_same_split"
CHAMPION_VERSION_PARAM = "champion_version"
POP_METRIC = "baseline_pop_val_ndcg_at_10"

FIRST = "first"
SAME_SPLIT = "same_split"
LEGACY = "legacy"


@dataclass(frozen=True)
class Decision:
    promote: bool
    comparison: str  # FIRST | SAME_SPLIT | LEGACY
    reason: str


def decide_promotion(
    *,
    candidate_metric: float,
    production_version: str | None,
    production_metric: float | None,
    champion_version: str | None,
    champion_metric_same_split: float | None,
    min_delta: float = 0.0,
) -> Decision:
    """Pure decision (no I/O).

    1. No production version → promote (`first`).
    2. The candidate was scored against the *current* production version on
       its own split → promote only if strictly better by more than
       `min_delta` (`same_split`). A tie does not promote: retraining on
       identical data must not move the alias for nothing.
    3. Otherwise (run predates same-split scoring, or it scored a champion
       that is no longer production) → the old rule on each run's own metric,
       `candidate >= production` (`legacy`).
    """
    if production_version is None:
        return Decision(True, FIRST, "no production version yet")

    if champion_metric_same_split is not None and champion_version == production_version:
        threshold = champion_metric_same_split + min_delta
        better = candidate_metric > threshold
        verb = ">" if better else "<="
        return Decision(
            better,
            SAME_SPLIT,
            f"candidate {candidate_metric:.4f} {verb} champion v{production_version} "
            f"on the same split {champion_metric_same_split:.4f} + min_delta {min_delta}",
        )

    if champion_metric_same_split is None:
        why = "candidate run has no same-split champion score"
    else:
        why = (f"candidate was scored against champion v{champion_version}, "
               f"production is now v{production_version}")
    if production_metric is None:
        return Decision(False, LEGACY, f"{why}; production run has no {METRIC_NAME}")
    better = candidate_metric >= production_metric
    return Decision(
        better,
        LEGACY,
        f"{why}; own-split {candidate_metric:.4f} {'>=' if better else '<'} {production_metric:.4f}",
    )


def lift_over_pop(metrics: dict[str, float]) -> float | None:
    """`val_ndcg_at_10 / baseline_pop_val_ndcg_at_10` of one run — reporting
    only (how much better than popularity on that run's own split)."""
    pop = metrics.get(POP_METRIC)
    value = metrics.get(METRIC_NAME)
    if not pop or value is None:
        return None
    return value / pop


def _latest_version(client: MlflowClient, name: str) -> str:
    versions = client.search_model_versions(f"name='{name}'")
    if not versions:
        raise RuntimeError(f"No registered versions found for model {name!r}")
    return max(versions, key=lambda version: int(version.version)).version


def _run_of_version(client: MlflowClient, version):
    return client.get_run(version.run_id)


def main() -> int:
    parser = argparse.ArgumentParser(description="Promote a Phase 4 model version if it improves on production.")
    parser.add_argument("--model-name", default=REGISTERED_MODEL_NAME)
    parser.add_argument("--version", default=None, help="Defaults to the latest registered version.")
    parser.add_argument("--min-delta", type=float, default=0.0,
                        help="Candidate must beat the champion (same split) by MORE than this much.")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the decision but do not move the `production` alias.")
    args = parser.parse_args()

    client = get_client()
    candidate_version = args.version or _latest_version(client, args.model_name)
    candidate = client.get_model_version(args.model_name, candidate_version)
    candidate_run = _run_of_version(client, candidate)
    if METRIC_NAME not in candidate_run.data.metrics:
        raise RuntimeError(f"Run {candidate.run_id!r} has no metric {METRIC_NAME!r}")
    candidate_metrics = candidate_run.data.metrics

    production = get_production_version(args.model_name, client=client)
    production_metrics = _run_of_version(client, production).data.metrics if production else {}

    decision = decide_promotion(
        candidate_metric=candidate_metrics[METRIC_NAME],
        production_version=production.version if production else None,
        production_metric=production_metrics.get(METRIC_NAME),
        champion_version=candidate_run.data.params.get(CHAMPION_VERSION_PARAM),
        champion_metric_same_split=candidate_metrics.get(SAME_SPLIT_METRIC),
        min_delta=args.min_delta,
    )
    if decision.promote and not args.dry_run:
        set_production_version(args.model_name, candidate_version, client=client)

    print(json.dumps({
        "model_name": args.model_name,
        "candidate_version": candidate_version,
        "candidate_metric": candidate_metrics[METRIC_NAME],
        "production_version_before": production.version if production else None,
        "production_metric_before": production_metrics.get(METRIC_NAME),
        "promoted": decision.promote and not args.dry_run,
        "would_promote": decision.promote,
        "dry_run": args.dry_run,
        "comparison": decision.comparison,
        "reason": decision.reason,
        "champion_metric_same_split": candidate_metrics.get(SAME_SPLIT_METRIC),
        "candidate_lift_over_pop": lift_over_pop(candidate_metrics),
        "production_lift_over_pop": lift_over_pop(production_metrics),
        "min_delta": args.min_delta,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
