FROM python:3.12.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

# torch installs from its own dedicated CPU-only index, in a separate `pip
# install` from everything else — see infra/docker/training/Dockerfile for
# why this must not be mixed with the default-index install below.
COPY requirements/serving.txt /tmp/serving.txt
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.9.0

COPY libs/ /app/libs/
RUN pip install --no-cache-dir -e /app/libs -r /tmp/serving.txt

COPY feature_repo/ /app/feature_repo/
COPY serving/ /app/serving/

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/feature_repo/data \
    && chown -R appuser:appuser /app

USER appuser

HEALTHCHECK --interval=10s --timeout=5s --retries=12 --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)"

# This is a long-running service, not a one-shot job — no
# ENTRYPOINT ["python","-m"] like the batch jobs.
CMD ["uvicorn", "serving.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
