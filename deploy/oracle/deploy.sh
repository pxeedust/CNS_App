#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
dc() { docker compose --env-file .env.production "$@"; }
test -f .env.production || { echo 'Create .env.production first; see deploy/oracle/README.md.' >&2; exit 1; }
mkdir -p secrets
dc config --quiet
dc build
# Stop job producers first; allow existing worker tasks to finish before migration.
dc stop web worker
dc up -d --wait db redis
dc run --rm setup
dc up -d web worker proxy
dc ps
