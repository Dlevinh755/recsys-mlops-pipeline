"""Common `default_args`/kwargs shared by every Phase 6 DAG — kept here,
not duplicated per file. DAGs only chain `JobDockerOperator` tasks calling
existing jobs (CLAUDE.md nguyên tắc #1: no business logic in a DAG)."""

from __future__ import annotations

from datetime import datetime

DAG_KWARGS = dict(
    schedule=None,  # manual trigger only. Phase 6 chose no automatic chaining;
                     # dag_continuous_training (ADR 0014) is the one exception:
                     # it triggers these DAGs in order, so they must not be
                     # born paused (a run of a paused DAG never starts).
    is_paused_upon_creation=False,
    start_date=datetime(2026, 1, 1),
    catchup=False,
    default_args={"retries": 0},
    tags=["phase-6"],
)
