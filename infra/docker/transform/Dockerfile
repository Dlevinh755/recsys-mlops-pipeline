FROM python:3.12.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY libs/ /app/libs/
COPY requirements/transform.txt /tmp/transform.txt
COPY requirements/dev.txt /tmp/dev.txt
# `requirements/dev.txt` (ruff — `pytest` is already in transform.txt) also
# installs here (Phase 8): Jenkinsfile's `Lint`/`Unit Test`/`Integration
# Test` stages all run inside this exact image rather than a separate CI
# toolchain — see docs/modules/phase-8-cicd.md. `dev.txt`'s own first line
# (`-e ../libs`, a host-relative path meant for `pip install` run from
# `recommendation-mlops/`) is stripped before installing it here — `-e
# /app/libs` right before it already does the same install with the correct
# in-container path.
RUN pip install --no-cache-dir -e /app/libs -r /tmp/transform.txt \
    && grep -v '^-e ' /tmp/dev.txt > /tmp/dev-tools.txt \
    && pip install --no-cache-dir -r /tmp/dev-tools.txt

COPY jobs/__init__.py /app/jobs/__init__.py
COPY jobs/transform/ /app/jobs/transform/
COPY jobs/features/ /app/jobs/features/
COPY jobs/maintenance/ /app/jobs/maintenance/
COPY sql/ /app/sql/
COPY tests/unit/ /app/tests/unit/
COPY tests/integration/ /app/tests/integration/
COPY tests/data_quality/ /app/tests/data_quality/
COPY scripts/duckdb_query_iceberg.py /app/scripts/duckdb_query_iceberg.py

RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app

USER appuser

# build_bronze / build_silver / build_gold / build_user_features /
# compact_iceberg (Phase 7) all ship in this one image (same
# DuckDB/PyIceberg dependency set) — override the module at
# `docker compose run` time, e.g. `docker compose run --rm
# transform-job jobs.features.build_user_features`.
ENTRYPOINT ["python", "-m"]
CMD ["jobs.transform.build_bronze"]
