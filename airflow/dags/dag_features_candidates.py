"""Phase 6 — `jobs/features/build_user_features.py` (Silver -> Gold user
sequence, input GRU4Rec). Runs in the `transform-job` image (same
DuckDB/PyIceberg dependency set, no separate image needed — Phase 2/3
convention).

`similar_items` (the "candidates" half of this DAG's original name in
`de-xuat-trien-khai.md`) is intentionally NOT here: it reads the
`production`-aliased model's embeddings, so it must run after training
finishes — it is the last task of `dag_training.py` instead. See
`docs/modules/phase-6-orchestration.md` mục "khác biệt so với đề xuất
ban đầu"."""

from __future__ import annotations

from airflow import DAG

from operators.job_docker_operator import JobDockerOperator

from _defaults import DAG_KWARGS

with DAG(dag_id="dag_features_candidates", **DAG_KWARGS):
    JobDockerOperator(
        task_id="build_user_features",
        compose_service="transform-job",
        module="jobs.features.build_user_features",
    )
