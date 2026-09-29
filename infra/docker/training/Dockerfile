FROM python:3.12.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

# torch installs from its own dedicated CPU-only index, in a separate `pip
# install` from everything else — mixing `--index-url` with a normal PyPI
# install in one invocation risks pip resolving *other* packages (mlflow,
# pyiceberg, pandas via `-e /app/libs`) against the wrong index too, since
# download.pytorch.org only hosts torch/torchvision/torchaudio wheels.
COPY requirements/training.txt /tmp/training.txt
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu -r /tmp/training.txt

COPY libs/ /app/libs/
COPY requirements/dev.txt /tmp/dev.txt
# redis (used by jobs/candidates/similar_items.py, Phase 5) installs here,
# from the default PyPI index — not in requirements/training.txt, which is
# installed above against PyTorch's CPU-only index that doesn't host it.
# `requirements/dev.txt` (pytest/ruff) also installs here (Phase 8): the
# Jenkinsfile's `Model Smoke Test` stage runs `pytest` *inside this exact
# image* rather than a separate CI toolchain — see
# docs/modules/phase-8-cicd.md and PHONG-VAN-QA-BAO-VE.md mục 4. `dev.txt`'s
# own `-e ../libs` line (host-relative) is stripped first — `-e /app/libs`
# right before it already installs the same package with the right path.
RUN pip install --no-cache-dir -e /app/libs redis==5.2.1 \
    && grep -v '^-e ' /tmp/dev.txt > /tmp/dev-tools.txt \
    && pip install --no-cache-dir -r /tmp/dev-tools.txt

COPY jobs/__init__.py /app/jobs/__init__.py
COPY jobs/training/ /app/jobs/training/
COPY jobs/candidates/ /app/jobs/candidates/
COPY tests/model/ /app/tests/model/
COPY tests/unit/training/ /app/tests/unit/training/
# Not `serving`'s own image — this one test needs `torch` (which `serving`
# also carries) to create real model versions; see that file's own
# docstring for why it lives here instead of tests/model/.
COPY serving/tests/test_model_reload.py /app/serving/tests/test_model_reload.py
COPY serving/tests/__init__.py /app/serving/tests/__init__.py
COPY serving/__init__.py /app/serving/__init__.py

RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app

USER appuser

# build_sequence_dataset / train_sequence / evaluate / promote all ship in
# this one image — override the module at `docker compose run` time, e.g.
# `docker compose run --rm training-job jobs.training.promote`.
ENTRYPOINT ["python", "-m"]
CMD ["jobs.training.train_sequence"]
