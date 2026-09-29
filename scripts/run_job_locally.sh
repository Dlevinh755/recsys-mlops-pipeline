#!/usr/bin/env bash
set -euo pipefail

job="${1:-}"

case "$job" in
  extract)
    docker compose --profile jobs build extract-job
    docker compose --profile jobs run --rm extract-job
    ;;
  *)
    echo "Usage: $0 extract" >&2
    exit 2
    ;;
esac
