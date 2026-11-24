"""Shared `DockerOperator` wrapper for every Phase 6 DAG.

Environment for each job comes straight from `docker-compose.yml` via
`docker compose config --format json` — that file is already the single
source of truth for how each job runs (Phase 1-5), so the DAGs must not
hard-code a second copy of these environment blocks (would drift the
moment one is edited without the other). Job services use `profiles:
[jobs]`, so `config` needs `--profile jobs` or they're silently omitted.

The image name is also read from that output: since Phase 8 every `*-job`
service declares `image: ${DOCKERHUB_USERNAME:-local}/mlops4rec-<x>:${IMAGE_TAG:-dev}`,
which `docker compose config` returns already resolved. So setting
`IMAGE_TAG=<git sha>` in the repo's `.env` (or in the scheduler's environment)
makes every task run the image Jenkins built and pushed for that commit
(verified 2026-11-24: `dag_monitoring.model_quality_report` ran from
`dlvinh755/mlops4rec-monitoring:7156423`). The config is cached per process
(`_compose_config`), so restart the scheduler after changing the tag. The old
`<project>-<service>:latest` name only exists for build-only services and is
kept as a fallback.

This requires the
Airflow image to have the Docker CLI + Compose plugin installed
(`infra/docker/airflow/Dockerfile`) and the repo mounted read-only at
`AIRFLOW_PROJECT_DIR` (docker-compose.yml `airflow-webserver`/`-scheduler`
volumes), plus the host's Docker socket for Docker-outside-of-Docker.

DAGs only pass `compose_service` (which `*-job` image to run) and,
for images that ship several jobs behind one `ENTRYPOINT ["python", "-m"]`
(see e.g. `infra/docker/transform/Dockerfile`), `module` (the CLI arg that
overrides the image's default `CMD` — exactly what
`docker compose run --rm <service> <module>` already does today).
"""

from __future__ import annotations

import json
import os
import subprocess
from functools import lru_cache

from airflow.providers.docker.operators.docker import DockerOperator

PROJECT_DIR = os.environ.get("AIRFLOW_PROJECT_DIR", "/opt/airflow/project")


@lru_cache(maxsize=1)
def _compose_config() -> dict:
    result = subprocess.run(
        ["docker", "compose", "--project-directory", PROJECT_DIR, "--profile", "jobs", "config", "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def _project_name() -> str:
    return os.environ.get("COMPOSE_PROJECT_NAME", "recommendation-mlops")


def _network_name() -> str:
    return f"{_project_name()}-net"


class JobDockerOperator(DockerOperator):
    def __init__(self, *, compose_service: str, module: str | None = None, **kwargs) -> None:
        service = _compose_config()["services"][compose_service]
        super().__init__(
            # Since Phase 8 every job service declares `image:` (tag
            # `${DOCKERHUB_USERNAME:-local}/mlops4rec-<x>:${IMAGE_TAG:-dev}`),
            # and `docker compose config` returns it resolved — that is the
            # image `docker compose build`/`make` actually produces. The old
            # `<project>-<service>:latest` name only exists for build-only
            # services, so keep it as a fallback.
            image=service.get("image") or f"{_project_name()}-{compose_service}:latest",
            command=[module] if module else None,
            environment=service.get("environment", {}),
            docker_url="unix://var/run/docker.sock",
            network_mode=_network_name(),
            auto_remove="success",
            mount_tmp_dir=False,
            **kwargs,
        )
