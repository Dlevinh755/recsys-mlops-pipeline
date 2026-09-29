#!/bin/sh
set -eu

alias_name=local
endpoint=http://minio:9000

until mc alias set "$alias_name" "$endpoint" "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null 2>&1; do
  echo "Waiting for MinIO..."
  sleep 2
done

mc mb --ignore-existing "$alias_name/$ICEBERG_BUCKET"
mc mb --ignore-existing "$alias_name/$MLFLOW_ARTIFACT_BUCKET"
mc mb --ignore-existing "$alias_name/$EXTRACT_STAGING_BUCKET"
mc mb --ignore-existing "$alias_name/$FEAST_OFFLINE_STORE_BUCKET"
mc mb --ignore-existing "$alias_name/$MONITORING_BASELINE_BUCKET"
mc mb --ignore-existing "$alias_name/$MONITORING_REPORTS_BUCKET"
mc anonymous set none "$alias_name/$ICEBERG_BUCKET"
mc anonymous set none "$alias_name/$MLFLOW_ARTIFACT_BUCKET"
mc anonymous set none "$alias_name/$EXTRACT_STAGING_BUCKET"
mc anonymous set none "$alias_name/$FEAST_OFFLINE_STORE_BUCKET"
mc anonymous set none "$alias_name/$MONITORING_BASELINE_BUCKET"
mc anonymous set none "$alias_name/$MONITORING_REPORTS_BUCKET"

echo "MinIO buckets are ready: $ICEBERG_BUCKET, $MLFLOW_ARTIFACT_BUCKET, $EXTRACT_STAGING_BUCKET, $FEAST_OFFLINE_STORE_BUCKET, $MONITORING_BASELINE_BUCKET, $MONITORING_REPORTS_BUCKET"
