ARG MLFLOW_IMAGE=ghcr.io/mlflow/mlflow:v3.12.0
FROM ${MLFLOW_IMAGE}

RUN python -m pip install --no-cache-dir \
    boto3==1.40.1 \
    psycopg2-binary==2.9.10
