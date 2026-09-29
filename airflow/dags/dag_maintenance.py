"""Phase 7 — compact Bronze Iceberg tables (`jobs/maintenance/compact_iceberg.py`),
deferred from Phase 2/6 (see that module's docstring for why only Bronze)."""

from __future__ import annotations

from airflow import DAG

from operators.job_docker_operator import JobDockerOperator

from _defaults import DAG_KWARGS

with DAG(dag_id="dag_maintenance", **DAG_KWARGS):
    JobDockerOperator(
        task_id="compact_iceberg", compose_service="transform-job", module="jobs.maintenance.compact_iceberg"
    )
