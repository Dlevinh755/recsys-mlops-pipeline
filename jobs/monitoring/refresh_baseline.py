"""ADR 0014 — move the drift baseline to the model now in production.

When the `production` version differs from the `model_version` recorded in
`monitoring-baseline/current.meta.json`, rewrite `current.parquet` with the
Silver interactions that version trained on (`parent_asin` = `product_id`,
`rating`) and the meta `{model_version, rows, silver_snapshot_id, source,
created_at}`. Idempotent: same version → nothing to do. No promotion → the
baseline does not move (a rejected challenger changes nothing here).

Which Silver: the **snapshot the version trained on**
(`iceberg_snapshot_id.silver.interactions`, logged by `train_agf`) via time
travel. Two limits, both visible in the output:

* The version has **no such param** (trained before ADR 0014) → the baseline
  does not move (`refreshed: false`). Writing today's Silver would claim the
  model saw rows it never saw and hide real drift.
* The snapshot was expired (`expire_snapshots`, ADR 0012) → fall back to the
  current Silver, recorded as `meta.source = "silver_current"`: rows that
  arrived between training and now leak into the baseline.
"""

from __future__ import annotations

import json
import sys

import pandas as pd

from jobs.monitoring.baseline import baseline_filesystem, read_meta, write_baseline
from jobs.transform.iceberg_writer import get_or_create_table
from reco_mlops_libs.common.env import require_env
from reco_mlops_libs.iceberg.catalog import get_catalog
from reco_mlops_libs.iceberg.schema_contracts import silver_interactions
from reco_mlops_libs.mlflow_utils.registry_client import get_client, get_production_version
from reco_mlops_libs.ranking.base import REGISTERED_MODEL_NAME

SNAPSHOT_PARAM = f"iceberg_snapshot_id.{silver_interactions.TABLE_IDENTIFIER}"
FIELDS = ("product_id", "rating")


def read_silver(table, snapshot_id: str | None) -> tuple[pd.DataFrame, str, int | None]:
    """`(frame, source, snapshot_id_used)`; time travel when possible."""
    if snapshot_id not in (None, "", "None"):
        try:
            frame = table.scan(snapshot_id=int(snapshot_id), selected_fields=FIELDS).to_pandas()
            return frame, "silver_snapshot", int(snapshot_id)
        except Exception as error:  # noqa: BLE001 - expired snapshot, missing files...
            print(json.dumps({"warning": f"snapshot {snapshot_id} not readable ({type(error).__name__}); "
                                         "using current Silver"}))
    current = table.current_snapshot()
    frame = table.scan(selected_fields=FIELDS).to_pandas()
    return frame, "silver_current", current.snapshot_id if current else None


def to_baseline_frame(silver: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({"parent_asin": silver["product_id"], "rating": silver["rating"].astype(float)})


def refresh(client, fs, bucket: str, table, prefix: str = "") -> dict:
    production = get_production_version(REGISTERED_MODEL_NAME, client=client)
    if production is None:
        return {"refreshed": False, "reason": "no production version"}
    meta = read_meta(fs, bucket, prefix)
    if meta is not None and str(meta.get("model_version")) == str(production.version):
        return {"refreshed": False, "reason": "baseline already tracks this version",
                "model_version": production.version}

    snapshot_param = client.get_run(production.run_id).data.params.get(SNAPSHOT_PARAM)
    if snapshot_param is None:
        return {"refreshed": False, "reason": f"version {production.version} has no {SNAPSHOT_PARAM} "
                                                "(trained before ADR 0014); baseline unchanged",
                "model_version": production.version}
    silver, source, snapshot_id = read_silver(table, snapshot_param)
    written = write_baseline(
        fs, bucket, to_baseline_frame(silver),
        {"model_version": str(production.version), "silver_snapshot_id": snapshot_id, "source": source},
        prefix,
    )
    return {"refreshed": True, **written}


def main() -> int:
    table = get_or_create_table(get_catalog(), silver_interactions.TABLE_IDENTIFIER,
                                silver_interactions.ICEBERG_SCHEMA)
    result = refresh(get_client(), baseline_filesystem(), require_env("MONITORING_BASELINE_BUCKET"), table)
    print(json.dumps({"refresh_baseline_result": result}, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
