"""Common `default_args`/kwargs shared by every Phase 6 DAG — kept here,
not duplicated per file. DAGs only chain `JobDockerOperator` tasks calling
existing jobs (CLAUDE.md nguyên tắc #1: no business logic in a DAG)."""

from __future__ import annotations

from datetime import datetime

DAG_KWARGS = dict(
    schedule=None,  # manual trigger only — DoD only requires triggering
                     # dag_ingest -> dag_transform -> dag_training ->
                     # dag_materialize in order from the UI, not automatic
                     # cross-DAG chaining (out of scope for this MVP).
    start_date=datetime(2026, 1, 1),
    catchup=False,
    default_args={"retries": 0},
    tags=["phase-6"],
)
