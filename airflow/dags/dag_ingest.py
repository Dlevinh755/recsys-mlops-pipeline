"""Phase 6 — `python -m jobs.extract.run` (dlt-native incremental extract,
ADR 0006), via `extract-job`'s fixed `ENTRYPOINT` (no module override)."""

from __future__ import annotations

from airflow import DAG

from operators.job_docker_operator import JobDockerOperator

from _defaults import DAG_KWARGS

with DAG(dag_id="dag_ingest", **DAG_KWARGS):
    JobDockerOperator(task_id="extract", compose_service="extract-job")
