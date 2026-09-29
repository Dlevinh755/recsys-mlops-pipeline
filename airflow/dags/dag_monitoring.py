"""Phase 7 — data/model quality reports (`jobs/monitoring/`), independent
tasks (neither depends on the other's output)."""

from __future__ import annotations

from airflow import DAG

from operators.job_docker_operator import JobDockerOperator

from _defaults import DAG_KWARGS

with DAG(dag_id="dag_monitoring", **DAG_KWARGS):
    JobDockerOperator(
        task_id="data_drift_report", compose_service="monitoring-job", module="jobs.monitoring.data_drift_report"
    )
    JobDockerOperator(
        task_id="model_quality_report",
        compose_service="monitoring-job",
        module="jobs.monitoring.model_quality_report",
    )
