"""Phase 7 — model quality signals, scoped down from full prediction drift
(see `docs/modules/phase-7-monitoring.md` "khác biệt so với đề xuất ban
đầu": `serving` doesn't log predictions anywhere durable, so there is no
real served-prediction history to run Evidently's prediction-drift checks
against; adding that log would be a second architectural exception beyond
`POST /interact`, out of scope for a monitoring-only phase).

Two real, already-available signals instead:

1. **Vocab coverage** — the fraction of currently active products
   (`gold.item_features`) the `production`-aliased model actually knows
   (its `item_vocab.json`, same artifact `serving/app/core/model_loader.py`
   downloads to build the ranker). Drops over time as new products appear
   without a retrain — a genuine, recommendation-specific quality-drift
   signal.
2. **Metric trend across versions** — `val_ndcg_at_10` (the same metric
   `jobs/training/promote.py` gates on) for every registered version, so a
   regression across versions is visible in one place instead of only the
   single latest-vs-current comparison `promote.py` makes.
"""

from __future__ import annotations

import json

from reco_mlops_libs.iceberg.catalog import get_catalog
from reco_mlops_libs.iceberg.schema_contracts import gold_item_features
from reco_mlops_libs.mlflow_utils.registry_client import get_client, get_production_version

from jobs.monitoring.report_sink import save_report
from jobs.transform.iceberg_writer import get_or_create_table

# Must match jobs/training/train_sequence.py::REGISTERED_MODEL_NAME /
# jobs/training/promote.py::METRIC_NAME — monitoring-job cannot import
# jobs.training directly (CLAUDE.md nguyên tắc #2: no cross-image
# dependency between job packages).
REGISTERED_MODEL_NAME = "reco-mlops-gru4rec"
METRIC_NAME = "val_ndcg_at_10"


def active_item_ids() -> set[str]:
    catalog = get_catalog()
    table = get_or_create_table(catalog, gold_item_features.TABLE_IDENTIFIER, gold_item_features.ICEBERG_SCHEMA)
    return set(table.scan(selected_fields=("product_id",)).to_arrow()["product_id"].to_pylist())


def vocab_coverage(client, version, active_ids: set[str]) -> dict:
    vocab_path = client.download_artifacts(version.run_id, "item_vocab.json")
    with open(vocab_path, encoding="utf-8") as file:
        vocab: dict[str, int] = json.load(file)
    known = active_ids & vocab.keys()
    return {
        "model_version": version.version,
        "active_items": len(active_ids),
        "items_known_to_model": len(known),
        "coverage": round(len(known) / len(active_ids), 4) if active_ids else None,
    }


def metric_trend(client) -> list[dict]:
    versions = client.search_model_versions(f"name='{REGISTERED_MODEL_NAME}'")
    trend = []
    for version in sorted(versions, key=lambda v: int(v.version)):
        run = client.get_run(version.run_id)
        trend.append({"version": version.version, METRIC_NAME: run.data.metrics.get(METRIC_NAME)})
    return trend


def main() -> int:
    client = get_client()
    production_version = get_production_version(REGISTERED_MODEL_NAME, client)

    result: dict = {"metric_trend": metric_trend(client)}
    if production_version is None:
        result["vocab_coverage"] = None
        result["note"] = "no version aliased 'production' yet"
    else:
        result["vocab_coverage"] = vocab_coverage(client, production_version, active_item_ids())

    paths = save_report("model_quality", data=result)
    print(json.dumps({"model_quality_result": {**result, "report_paths": paths}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
