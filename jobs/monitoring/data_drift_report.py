"""Phase 7 — Evidently data drift: baseline (`train.parquet`, the same
80% "seed" period every other Phase 1-4 baseline in this project uses —
see `amazone_data/DATA.md`) vs current `silver.interactions`.

Baseline lives on MinIO (`MONITORING_BASELINE_BUCKET`), not read from the
host `amazone_data/` path directly: this job runs inside `monitoring-job`
(containerized, triggered by Airflow), which has no access to host files —
only `scripts/*.py` run directly on the host do (see
`scripts/upload_monitoring_baseline.py`, run once to seed this bucket).
Since ADR 0014 the baseline follows the production model: `current.parquet`
(written by `refresh_baseline.py`) when it exists, else `train.parquet`
(`baseline.py`). The saved JSON gains a `reco_summary` key (dataset_drift,
drifted columns, row counts) that `retrain_decision.py` reads.

Compared columns: `rating` (present verbatim in both datasets) and the
derived boolean `is_cold_start_item` (`product_id` absent from the
baseline's item set) — this is exactly the 30.1% cold-start phenomenon
`amazone_data/DATA.md` mục 4.1 documents, a real, already-known drift
signal, not a synthetic one made up for this report.
"""

from __future__ import annotations

import json

import pandas as pd
from evidently.metric_preset import DataDriftPreset
from evidently.report import Report

from jobs.monitoring.baseline import baseline_filesystem
from jobs.monitoring.baseline import load_baseline as load_baseline_from
from jobs.monitoring.report_sink import save_report
from jobs.transform.iceberg_writer import get_or_create_table
from reco_mlops_libs.common.env import require_env
from reco_mlops_libs.iceberg.catalog import get_catalog
from reco_mlops_libs.iceberg.schema_contracts import silver_interactions


def load_baseline() -> tuple[pd.DataFrame, dict]:
    """`(frame, info)` — see `jobs.monitoring.baseline.load_baseline`."""
    return load_baseline_from(baseline_filesystem(), require_env("MONITORING_BASELINE_BUCKET"))


def load_current() -> pd.DataFrame:
    catalog = get_catalog()
    table = get_or_create_table(catalog, silver_interactions.TABLE_IDENTIFIER, silver_interactions.ICEBERG_SCHEMA)
    return table.scan(selected_fields=("product_id", "rating")).to_pandas()


def build_comparison_frames(baseline: pd.DataFrame, current: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Pure (no I/O), so unit-testable without MinIO/Iceberg."""
    known_items = set(baseline["parent_asin"])
    reference = pd.DataFrame({"rating": baseline["rating"].astype(float), "is_cold_start_item": False})
    current_frame = pd.DataFrame(
        {
            "rating": current["rating"].astype(float),
            "is_cold_start_item": ~current["product_id"].isin(known_items),
        }
    )
    return reference, current_frame


def extract_drift_summary(report_dict: dict) -> dict:
    """Pure: `report.as_dict()` of a `DataDriftPreset` run → the facts
    `retrain_decision` needs. Evidently's `DatasetDriftMetric` carries the
    dataset-level verdict, `DataDriftTable` the per-column scores."""
    summary: dict = {"dataset_drift": None, "share_of_drifted_columns": None, "drifted_columns": [],
                     "column_scores": {}}
    for metric in report_dict.get("metrics", []):
        result = metric.get("result", {})
        if metric.get("metric") == "DatasetDriftMetric":
            summary["dataset_drift"] = bool(result["dataset_drift"])
            summary["share_of_drifted_columns"] = float(result["share_of_drifted_columns"])
        elif metric.get("metric") == "DataDriftTable":
            for column, detail in result.get("drift_by_columns", {}).items():
                summary["column_scores"][column] = {
                    "drift_score": float(detail["drift_score"]),
                    "drift_detected": bool(detail["drift_detected"]),
                    "stattest": detail.get("stattest_name"),
                }
                if detail["drift_detected"]:
                    summary["drifted_columns"].append(column)
    if summary["dataset_drift"] is None:
        raise RuntimeError("Evidently report has no DatasetDriftMetric — preset changed?")
    return summary


def main() -> int:
    baseline, baseline_info = load_baseline()
    current = load_current()
    reference, current_frame = build_comparison_frames(baseline, current)

    report = Report(metrics=[DataDriftPreset()])
    report.run(reference_data=reference, current_data=current_frame)

    cold_start_ratio = float(current_frame["is_cold_start_item"].mean())
    drift = extract_drift_summary(report.as_dict())
    summary = {
        "baseline_source": baseline_info["source"],
        "baseline_model_version": baseline_info.get("model_version"),
        "baseline_rows": len(reference),
        "current_rows": len(current_frame),
        "current_cold_start_ratio": round(cold_start_ratio, 4),
        **drift,
    }
    # `reco_summary` is additive: the rest of the file stays Evidently's own dict.
    paths = save_report("data_drift", html=report.get_html(), data={**report.as_dict(), "reco_summary": summary})
    summary["report_paths"] = paths
    print(json.dumps({"data_drift_result": summary}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
