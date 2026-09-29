"""Phase 7 — Evidently data drift: baseline (`train.parquet`, the same
80% "seed" period every other Phase 1-4 baseline in this project uses —
see `amazone_data/DATA.md`) vs current `silver.interactions`.

Baseline lives on MinIO (`MONITORING_BASELINE_BUCKET`), not read from the
host `amazone_data/` path directly: this job runs inside `monitoring-job`
(containerized, triggered by Airflow), which has no access to host files —
only `scripts/*.py` run directly on the host do (see
`scripts/upload_monitoring_baseline.py`, run once to seed this bucket).

Compared columns: `rating` (present verbatim in both datasets) and the
derived boolean `is_cold_start_item` (`product_id` absent from the
baseline's item set) — this is exactly the 30.1% cold-start phenomenon
`amazone_data/DATA.md` mục 4.1 documents, a real, already-known drift
signal, not a synthetic one made up for this report.
"""

from __future__ import annotations

import json

import pandas as pd
import s3fs
from evidently.metric_preset import DataDriftPreset
from evidently.report import Report

from jobs.monitoring.report_sink import save_report
from jobs.transform.iceberg_writer import get_or_create_table
from reco_mlops_libs.common.env import require_env
from reco_mlops_libs.iceberg.catalog import get_catalog
from reco_mlops_libs.iceberg.schema_contracts import silver_interactions

BASELINE_KEY = "train.parquet"


def _baseline_filesystem() -> s3fs.S3FileSystem:
    endpoint = require_env("MONITORING_S3_ENDPOINT")
    return s3fs.S3FileSystem(
        key=require_env("MONITORING_S3_ACCESS_KEY"),
        secret=require_env("MONITORING_S3_SECRET_KEY"),
        client_kwargs={"endpoint_url": endpoint},
        use_ssl=endpoint.startswith("https://"),
    )


def load_baseline() -> pd.DataFrame:
    bucket = require_env("MONITORING_BASELINE_BUCKET")
    fs = _baseline_filesystem()
    with fs.open(f"{bucket}/{BASELINE_KEY}", "rb") as file:
        return pd.read_parquet(file, columns=["parent_asin", "rating"])


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


def main() -> int:
    baseline = load_baseline()
    current = load_current()
    reference, current_frame = build_comparison_frames(baseline, current)

    report = Report(metrics=[DataDriftPreset()])
    report.run(reference_data=reference, current_data=current_frame)

    cold_start_ratio = float(current_frame["is_cold_start_item"].mean())
    paths = save_report("data_drift", html=report.get_html(), data=report.as_dict())

    summary = {
        "baseline_rows": len(reference),
        "current_rows": len(current_frame),
        "current_cold_start_ratio": round(cold_start_ratio, 4),
        "report_paths": paths,
    }
    print(json.dumps({"data_drift_result": summary}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
