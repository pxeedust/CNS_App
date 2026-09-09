#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."

dc() {
  docker compose \
    --env-file .env.production \
    -f compose.yaml \
    -f deploy/azure/compose.free.yaml \
    "$@"
}

test -f .env.production || {
  echo 'Create .env.production first; see deploy/azure/README.md.' >&2
  exit 1
}

mkdir -p secrets backups
dc config --quiet
dc build
dc stop web worker
dc up -d --wait db redis
dc run --rm setup
dc up -d web worker proxy
dc ps
