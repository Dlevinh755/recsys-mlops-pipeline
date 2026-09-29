FROM python:3.12.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY libs/ /app/libs/
COPY requirements/monitoring.txt /tmp/monitoring.txt
RUN pip install --no-cache-dir -e /app/libs -r /tmp/monitoring.txt

COPY jobs/__init__.py /app/jobs/__init__.py
COPY jobs/transform/iceberg_writer.py /app/jobs/transform/iceberg_writer.py
COPY jobs/transform/__init__.py /app/jobs/transform/__init__.py
COPY jobs/monitoring/ /app/jobs/monitoring/

RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app

USER appuser

# data_drift_report / model_quality_report ship in this one image (both need
# evidently, which is too heavy to add to transform-job/training-job just
# for this) — override the module at `docker compose run` time.
ENTRYPOINT ["python", "-m"]
CMD ["jobs.monitoring.data_drift_report"]
