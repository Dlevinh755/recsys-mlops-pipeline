"""Phase 4 — promotion gate: mark a newly trained model version as
`production` only if it beats the current champion (challenger/champion
pattern), never by an arbitrary hard-coded quality floor. The very first
version ever trained for a model name is always promoted (there is no
champion yet to compare against).
"""

from __future__ import annotations

import argparse
import json

from mlflow import MlflowClient

from jobs.training.train_sequence import REGISTERED_MODEL_NAME
from reco_mlops_libs.mlflow_utils.registry_client import (
    get_client,
    get_production_version,
    set_production_version,
)

METRIC_NAME = "val_ndcg_at_10"


def _latest_version(client: MlflowClient, name: str) -> str:
    versions = client.search_model_versions(f"name='{name}'")
    if not versions:
        raise RuntimeError(f"No registered versions found for model {name!r}")
    return max(versions, key=lambda version: int(version.version)).version


def _metric_of_version(client: MlflowClient, version) -> float:
    run = client.get_run(version.run_id)
    if METRIC_NAME not in run.data.metrics:
        raise RuntimeError(f"Run {version.run_id!r} has no metric {METRIC_NAME!r}")
    return run.data.metrics[METRIC_NAME]


def main() -> int:
    parser = argparse.ArgumentParser(description="Promote a Phase 4 model version if it improves on production.")
    parser.add_argument("--model-name", default=REGISTERED_MODEL_NAME)
    parser.add_argument("--version", default=None, help="Defaults to the latest registered version.")
    args = parser.parse_args()

    client = get_client()
    candidate_version = args.version or _latest_version(client, args.model_name)
    candidate = client.get_model_version(args.model_name, candidate_version)
    candidate_metric = _metric_of_version(client, candidate)

    production = get_production_version(args.model_name, client=client)
    production_metric = _metric_of_version(client, production) if production else None

    should_promote = production is None or candidate_metric >= production_metric
    if should_promote:
        set_production_version(args.model_name, candidate_version, client=client)

    print(json.dumps({
        "model_name": args.model_name,
        "candidate_version": candidate_version,
        "candidate_metric": candidate_metric,
        "production_version_before": production.version if production else None,
        "production_metric_before": production_metric,
        "promoted": should_promote,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
