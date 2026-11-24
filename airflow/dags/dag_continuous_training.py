"""ADR 0014 — drift → retrain loop, wiring only (CLAUDE.md nguyên tắc #1: the
logic is in `jobs/monitoring/`).

    data_drift_report → retrain_decision ─(exit 99: skip)─→ everything below skipped
        → dag_ingest → dag_transform → dag_features_candidates
        → dag_materialize → dag_training → refresh_baseline

The existing DAGs are triggered, not copied: they keep working on their own.
`retrain_decision` exits 0 to retrain and 99 to skip; `skip_on_exit_code=99`
turns 99 into "skipped" for it and (default trigger rule) for every task
after it, so the run still ends green. `refresh_baseline` moves the drift
baseline only if `dag_training` actually promoted a new version.

Created paused with `@daily`: unpause for the schedule, or press Trigger for
a demo. Policy knobs: `.env.example` (`RETRAIN_*`).
"""

from __future__ import annotations

from airflow import DAG
from airflow.operators.trigger_dagrun import TriggerDagRunOperator

from operators.job_docker_operator import JobDockerOperator

from _defaults import DAG_KWARGS

CHAIN = ("dag_ingest", "dag_transform", "dag_features_candidates", "dag_materialize", "dag_training")

with DAG(dag_id="dag_continuous_training", max_active_runs=1,  # one retrain chain at a time
         **{**DAG_KWARGS, "schedule": "@daily", "is_paused_upon_creation": True}):
    data_drift_report = JobDockerOperator(
        task_id="data_drift_report", compose_service="monitoring-job", module="jobs.monitoring.data_drift_report"
    )
    retrain_decision = JobDockerOperator(
        task_id="retrain_decision",
        compose_service="monitoring-job",
        module="jobs.monitoring.retrain_decision",
        skip_on_exit_code=99,
    )
    refresh_baseline = JobDockerOperator(
        task_id="refresh_baseline", compose_service="monitoring-job", module="jobs.monitoring.refresh_baseline"
    )

    previous = data_drift_report >> retrain_decision
    for dag_id in CHAIN:
        trigger = TriggerDagRunOperator(
            task_id=f"run_{dag_id}",
            trigger_dag_id=dag_id,
            wait_for_completion=True,
            poke_interval=15,
            failed_states=["failed"],
        )
        previous = previous >> trigger
    previous >> refresh_baseline
