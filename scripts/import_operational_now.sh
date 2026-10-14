#!/usr/bin/env bash
# Import existing SQLite snapshot to native PostgreSQL operational tables.
# Data-only: does NOT restart services or modify SQLite.
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
snapshot="${1:-data/ramon_history.sqlite3}"
test -s "$snapshot" || { echo "SQLite snapshot not found: $snapshot" >&2; exit 1; }
test -s .secrets/postgres-password || { echo "Postgres secret missing" >&2; exit 1; }
docker image inspect ramon-ai-trader:pg-canary-working >/dev/null
net=$(docker inspect -f '{{range $name, $network := .NetworkSettings.Networks}}{{println $name}}{{end}}' ramon-ai-trader-postgres-logs-1 | head -n1)
test -n "$net" || { echo "PostgreSQL Docker network unavailable" >&2; exit 1; }
snapshot=$(realpath "$snapshot")
docker run --rm --network "$net" \
  -v "$PWD/src:/app/src:ro" \
  -v "$PWD/scripts:/app/scripts:ro" \
  -v "$snapshot:/data/source.sqlite3:ro" \
  -v "$PWD/.secrets/postgres-password:/run/secrets/postgres-password:ro" \
  -e PYTHONPATH=/app/src \
  --entrypoint python ramon-ai-trader:pg-canary-working \
  /app/scripts/import_sqlite_operational_pg.py \
  --sqlite /data/source.sqlite3 \
  --password-file /run/secrets/postgres-password
echo "Operational import attempted. Full-service cutover is NOT authorized by this step."
