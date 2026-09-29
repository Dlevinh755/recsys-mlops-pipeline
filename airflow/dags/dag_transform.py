"""Phase 6 — Bronze -> Silver -> Gold (`jobs/transform/`), same order as
`make bronze && make silver && make gold`."""

from __future__ import annotations

from airflow import DAG

from operators.job_docker_operator import JobDockerOperator

from _defaults import DAG_KWARGS

with DAG(dag_id="dag_transform", **DAG_KWARGS):
    build_bronze = JobDockerOperator(
        task_id="build_bronze", compose_service="transform-job", module="jobs.transform.build_bronze"
    )
    build_silver = JobDockerOperator(
        task_id="build_silver", compose_service="transform-job", module="jobs.transform.build_silver"
    )
    build_gold = JobDockerOperator(
        task_id="build_gold", compose_service="transform-job", module="jobs.transform.build_gold"
    )
    build_bronze >> build_silver >> build_gold
