"""Phase 6 — export Gold to Parquet then push into Feast/Redis online store
(`jobs/materialize/`), same order as
`make ... export_to_parquet && ... feast_materialize`."""

from __future__ import annotations

from airflow import DAG

from operators.job_docker_operator import JobDockerOperator

from _defaults import DAG_KWARGS

with DAG(dag_id="dag_materialize", **DAG_KWARGS):
    export_to_parquet = JobDockerOperator(
        task_id="export_to_parquet",
        compose_service="materialize-job",
        module="jobs.materialize.export_to_parquet",
    )
    feast_materialize = JobDockerOperator(
        task_id="feast_materialize",
        compose_service="materialize-job",
        module="jobs.materialize.feast_materialize",
    )
    export_to_parquet >> feast_materialize
