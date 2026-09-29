"""Phase 6 — train GRU4Rec, gate-promote to the `production` alias, then
refresh item-similarity candidates from the (possibly newly) promoted
model's embeddings.

`promote` needs no run_id passed via XCom: `jobs/training/promote.py`
defaults `--version` to the latest registered MLflow version, and
`train_sequence` always registers the version it just trained — so each
task is self-sufficient reading MLflow's own state, no data has to flow
through Airflow (CLAUDE.md nguyên tắc #1).

`jobs/training/evaluate.py` is NOT part of this chain — it requires
`--run-id` and is a manual analysis tool (see README), not an automated
step."""

from __future__ import annotations

from airflow import DAG

from operators.job_docker_operator import JobDockerOperator

from _defaults import DAG_KWARGS

with DAG(dag_id="dag_training", **DAG_KWARGS):
    train_sequence = JobDockerOperator(
        task_id="train_sequence", compose_service="training-job", module="jobs.training.train_sequence"
    )
    promote = JobDockerOperator(
        task_id="promote", compose_service="training-job", module="jobs.training.promote"
    )
    similar_items = JobDockerOperator(
        task_id="similar_items", compose_service="training-job", module="jobs.candidates.similar_items"
    )
    train_sequence >> promote >> similar_items
