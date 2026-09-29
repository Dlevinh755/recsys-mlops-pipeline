"""Data-to-model lineage helpers for MLflow runs — see
`docs/bao-cao-ky-thuat.md` mục 7.4: every model version must be traceable to
the exact Iceberg snapshot it trained on. Iceberg already has snapshots for
free, so this is one `log_param` call, done from the start (Phase 4), not
bolted on later.
"""

from __future__ import annotations

import mlflow
from pyiceberg.table import Table


def log_iceberg_snapshot(table: Table, table_identifier: str) -> None:
    """Log which Iceberg snapshot of `table_identifier` a training run read.

    Must be called inside an active `mlflow.start_run()`.
    """
    snapshot = table.current_snapshot()
    mlflow.log_param("iceberg_table", table_identifier)
    mlflow.log_param("iceberg_snapshot_id", snapshot.snapshot_id if snapshot else None)
