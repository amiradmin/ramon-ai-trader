#!/usr/bin/env bash
# Data-first, then core PostgreSQL API cutover. Does NOT retire SQLite.
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
compose=(docker compose -f compose.yaml -f compose.pg-core-cutover.yaml)
mode="${1:---check}"
case "$mode" in --check|--apply) ;; *) echo "Usage: $0 [--check|--apply]" >&2; exit 64;; esac
test -s .secrets/postgres-password || { echo "Missing PG secret" >&2; exit 1; }
test -s data/ramon_history.sqlite3 || { echo "Missing SQLite snapshot source" >&2; exit 1; }
docker image inspect ramon-ai-trader:pg-canary-working >/dev/null
"${compose[@]}" config --quiet
echo "PostgreSQL core cutover configuration: OK"
if [[ "$mode" == "--check" ]]; then
  echo "CHECK ONLY: data and live containers unchanged"
  exit 0
fi
# Data transfer must finish before any service restart.
bash scripts/migrate_data_first.sh
echo "Run focused API/storage tests after copy and before model restart"
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync python -m pytest -q \
  tests/test_postgres_operational_integration.py \
  tests/test_server_postgres_manual_paths.py \
  tests/test_mtf_collector.py \
  tests/test_training_dashboard_pg_path.py
echo "Starting PostgreSQL core services; unported dashboards and reports remain legacy"
"${compose[@]}" up -d --no-deps --no-build --force-recreate model
healthy=0
for _ in $(seq 1 60); do
  if curl -fsS --max-time 3 http://127.0.0.1:8012/health | python3 -c 'import json,sys; assert json.load(sys.stdin)["ready"]' >/dev/null 2>&1; then
    healthy=1
    break
  fi
  sleep 5
done
if [[ "$healthy" != 1 ]]; then
  echo "CORE MODEL NOT READY: preserve old SQLite DB, manual image/container rollback may be needed" >&2
  exit 1
fi
"${compose[@]}" up -d --no-deps --no-build --force-recreate mtf-collector training-dashboard
echo "CORE POSTGRES SERVICES STARTED. Not PostgreSQL-only: other readers still need migration."
