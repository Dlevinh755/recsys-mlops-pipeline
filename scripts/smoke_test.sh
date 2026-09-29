#!/bin/sh
set -eu

command -v docker >/dev/null 2>&1 || {
  echo "docker is required for the Phase 0 smoke test" >&2
  exit 1
}

curl --fail --silent --show-error "http://localhost:${MINIO_API_PORT:-9000}/minio/health/ready" >/dev/null
curl --fail --silent --show-error "http://localhost:${LAKEKEEPER_PORT:-8181}/health" >/dev/null
curl --fail --silent --show-error "http://localhost:${MLFLOW_PORT:-5000}/health" >/dev/null

product_count=$(docker compose exec -T source-db psql \
  -U "${SOURCE_DB_USER:-reco_app}" \
  -d "${SOURCE_DB_NAME:-recommendation}" \
  -Atc "SELECT count(*) FROM products;")

interaction_count=$(docker compose exec -T source-db psql \
  -U "${SOURCE_DB_USER:-reco_app}" \
  -d "${SOURCE_DB_NAME:-recommendation}" \
  -Atc "SELECT count(*) FROM interactions;")

[ "$product_count" -ge 1 ]
[ "$interaction_count" -ge 1 ]

docker compose run --rm --no-deps minio-init >/dev/null

echo "PASS: MinIO, Lakekeeper, MLflow and source seed data are ready"
echo "Seed rows: products=$product_count interactions=$interaction_count"
