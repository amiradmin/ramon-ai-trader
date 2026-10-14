#!/usr/bin/env bash
# Safe, scoped verification/recovery for legacy trade identifiers.
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
mode="${1:---check}"
[[ "$mode" == --check || "$mode" == --apply ]] || { echo "Usage: $0 --check|--apply" >&2; exit 64; }
test -s data/ramon_history.sqlite3
test -s .secrets/postgres-password
net=$(docker inspect -f '{{range $name,$value := .NetworkSettings.Networks}}{{println $name}}{{end}}' ramon-ai-trader-postgres-logs-1 | head -n1)
[[ -n "$net" ]]
args=(--sqlite /data/source.sqlite3 --password-file /run/secrets/postgres-password)
[[ "$mode" != --apply ]] || args+=(--apply)
docker run --rm --network "$net" \
  -v "$PWD/data/ramon_history.sqlite3:/data/source.sqlite3:ro" \
  -v "$PWD/scripts/recover_pg_missing_trades.py:/app/recover_pg_missing_trades.py:ro" \
  -v "$PWD/.secrets/postgres-password:/run/secrets/postgres-password:ro" \
  --entrypoint python ramon-ai-trader:pg-canary-working \
  /app/recover_pg_missing_trades.py "${args[@]}"
