#!/usr/bin/env bash
# Start only the independent PG training mirror; do not restart the live trader.
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
dc=(docker compose -f compose.yaml -f compose.pg-training.yaml --profile pg-training)
say(){ printf '\n== %s ==\n' "$*"; }
fail(){ echo "ERROR: $*" >&2; exit 1; }
command -v docker >/dev/null || fail "Docker not found"
docker info >/dev/null 2>&1 || fail "Docker daemon unavailable"
test -s .secrets/postgres-password || fail "Missing .secrets/postgres-password"
test -f data/ramon_history.sqlite3 || fail "SQLite source not found"
test -f compose.pg-training.yaml || fail "Sync compose override not found"
docker image inspect ramon-ai-trader:pg-canary-working >/dev/null 2>&1 || fail "Required tested image missing"
say "Validate compose (no trading container changes)"
"${dc[@]}" config --quiet
say "Check PostgreSQL"
docker compose --profile logging ps --status running postgres-logs
docker compose --profile logging exec -T postgres-logs sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' || fail "PostgreSQL unavailable"
say "Run targeted tests"
if command -v uv >/dev/null; then
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync python -m pytest -q tests/test_postgres_training_path.py
else
  fail "uv is required for the regression tests"
fi
say "Start only pg-training-sync"
"${dc[@]}" up -d --no-deps --no-build pg-training-sync
say "Worker status"
"${dc[@]}" ps pg-training-sync
say "Recent worker output (first scan may still be running)"
"${dc[@]}" logs --tail=15 pg-training-sync
say "Existing PostgreSQL row counts"
docker compose --profile logging exec -T postgres-logs sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT (SELECT count(*) FROM ramon_migration.training_decision_samples), (SELECT count(*) FROM ramon_migration.training_trade_outcomes)"'
echo "DONE: independent worker started; SQLite and the live model remain unchanged."
