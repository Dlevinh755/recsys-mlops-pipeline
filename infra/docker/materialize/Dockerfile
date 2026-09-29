FROM python:3.12.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY libs/ /app/libs/
COPY requirements/materialize.txt /tmp/materialize.txt
COPY requirements/dev.txt /tmp/dev.txt
# `requirements/dev.txt` (pytest) installs here too (Phase 8): this image is
# the only one with both `feast` and `pyiceberg`, so
# `tests/data_quality/test_train_serve_skew.py` (needs both, live) runs
# inside it — see docs/modules/phase-8-cicd.md. `dev.txt`'s own `-e ../libs`
# line (host-relative) is stripped first — `-e /app/libs` right before it
# already installs the same package with the right path.
RUN pip install --no-cache-dir -e /app/libs -r /tmp/materialize.txt \
    && grep -v '^-e ' /tmp/dev.txt > /tmp/dev-tools.txt \
    && pip install --no-cache-dir -r /tmp/dev-tools.txt

COPY jobs/__init__.py /app/jobs/__init__.py
COPY jobs/transform/iceberg_writer.py /app/jobs/transform/iceberg_writer.py
COPY jobs/transform/__init__.py /app/jobs/transform/__init__.py
COPY jobs/materialize/ /app/jobs/materialize/
COPY feature_repo/ /app/feature_repo/
COPY tests/data_quality/ /app/tests/data_quality/

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/feature_repo/data \
    && chown -R appuser:appuser /app

USER appuser

# export_to_parquet / feast_materialize ship in this one image (both need
# `feast`, which is too heavy a dependency to add to the lean transform-job
# image) — override the module at `docker compose run` time.
ENTRYPOINT ["python", "-m"]
CMD ["jobs.materialize.export_to_parquet"]
