"""Phase 7 — Iceberg maintenance: compact Bronze + Silver
(`jobs/maintenance/compact_iceberg.py`), then expire old snapshots and delete
the files only they used (`jobs/maintenance/expire_snapshots.py`,
docs/decisions/0012-iceberg-maintenance-expire-snapshots.md). Order matters:
compaction writes a new snapshot that supersedes the small files, expiry then
reclaims them. Run it when no `dag_transform` is running (both rewrite tables)."""

from __future__ import annotations

from airflow import DAG

from operators.job_docker_operator import JobDockerOperator

from _defaults import DAG_KWARGS

with DAG(dag_id="dag_maintenance", **DAG_KWARGS):
    compact = JobDockerOperator(
        task_id="compact_iceberg", compose_service="transform-job", module="jobs.maintenance.compact_iceberg"
    )
    expire = JobDockerOperator(
        task_id="expire_snapshots", compose_service="transform-job", module="jobs.maintenance.expire_snapshots"
    )
    compact >> expire
