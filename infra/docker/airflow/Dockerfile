# Phase 6 — Airflow image. Adds the Docker CLI + Compose plugin (not in the
# base image) so `JobDockerOperator` can run `docker compose config` to read
# each job's image/environment straight from docker-compose.yml (single
# source of truth, no duplicated env vars in DAG code — see
# docs/modules/phase-6-orchestration.md), and mounts the host's Docker
# socket at runtime (docker-compose.yml) to launch sibling job containers
# on the same network (Docker-outside-of-Docker).
ARG AIRFLOW_IMAGE=apache/airflow:2.10.4-python3.12
FROM ${AIRFLOW_IMAGE}

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian bookworm stable" \
        > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli docker-compose-plugin \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

USER airflow
COPY requirements/airflow.txt /tmp/airflow.txt
RUN pip install --no-cache-dir --constraint \
        "https://raw.githubusercontent.com/apache/airflow/constraints-2.10.4/constraints-3.12.txt" \
        -r /tmp/airflow.txt
