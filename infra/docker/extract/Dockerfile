FROM python:3.12.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY requirements/extract.txt /tmp/extract.txt
COPY requirements/dev.txt /tmp/dev.txt
# `requirements/dev.txt` (pytest) also installs here (Phase 8): Jenkinsfile's
# `Unit Test` stage runs `tests/unit/extract/` inside this exact image
# rather than a separate CI toolchain — see docs/modules/phase-8-cicd.md.
# `dev.txt`'s own `-e ../libs` line (host-relative) is stripped first —
# extract-job has no need for `reco_mlops_libs` otherwise, so there is no
# `-e /app/libs` install to duplicate here.
RUN pip install --no-cache-dir -r /tmp/extract.txt \
    && grep -v '^-e ' /tmp/dev.txt > /tmp/dev-tools.txt \
    && pip install --no-cache-dir -r /tmp/dev-tools.txt

COPY jobs/ /app/jobs/
COPY tests/unit/ /app/tests/unit/

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data/dlt-pipelines \
    && chown -R appuser:appuser /data/dlt-pipelines

USER appuser
ENTRYPOINT ["python", "-m", "jobs.extract.run"]
