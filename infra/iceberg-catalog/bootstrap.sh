#!/bin/sh
set -eu

catalog_url=http://lakekeeper:8181
project_id=00000000-0000-0000-0000-000000000000

post_json() {
  endpoint=$1
  payload=$2
  # Third argument: substring that marks an already-applied 400 response as
  # success, so this script is safe to run again against a catalog that was
  # bootstrapped by a previous `docker compose up` (named volumes persist
  # across container recreation).
  already_done_marker=${3:-}
  body_file=$(mktemp)
  status=$(curl --silent --show-error --output "$body_file" --write-out '%{http_code}' \
    --request POST "$endpoint" --header 'Content-Type: application/json' --data "$payload")

  case "$status" in
    200|201|204|409) ;;
    400)
      if [ -n "$already_done_marker" ] && grep -q "$already_done_marker" "$body_file"; then
        :
      else
        echo "Request failed: POST $endpoint returned HTTP $status" >&2
        sed -n '1,80p' "$body_file" >&2
        rm -f "$body_file"
        exit 1
      fi
      ;;
    *)
      echo "Request failed: POST $endpoint returned HTTP $status" >&2
      sed -n '1,80p' "$body_file" >&2
      rm -f "$body_file"
      exit 1
      ;;
  esac
  rm -f "$body_file"
}

post_json "$catalog_url/management/v1/bootstrap" '{"accept-terms-of-use":true}' "CatalogAlreadyBootstrapped"

warehouse_payload=$(printf '%s' "{
  \"warehouse-name\": \"$ICEBERG_WAREHOUSE_NAME\",
  \"project-id\": \"$project_id\",
  \"storage-profile\": {
    \"type\": \"s3\",
    \"bucket\": \"$ICEBERG_BUCKET\",
    \"key-prefix\": \"warehouse\",
    \"assume-role-arn\": null,
    \"endpoint\": \"http://minio:9000\",
    \"region\": \"$MINIO_REGION\",
    \"path-style-access\": true,
    \"flavor\": \"minio\",
    \"sts-enabled\": false
  },
  \"storage-credential\": {
    \"type\": \"s3\",
    \"credential-type\": \"access-key\",
    \"aws-access-key-id\": \"$MINIO_ROOT_USER\",
    \"aws-secret-access-key\": \"$MINIO_ROOT_PASSWORD\"
  }
}")

post_json "$catalog_url/management/v1/warehouse" "$warehouse_payload" "CreateWarehouseStorageProfileOverlap"
echo "Lakekeeper warehouse is ready: $ICEBERG_WAREHOUSE_NAME"
