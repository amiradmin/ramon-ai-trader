#!/usr/bin/env bash
# Safely deploy only the independent PostgreSQL training canary on HP-Mini.
# It never restarts model, dashboard, MT5, or the EA; it never deletes data.
set -Eeuo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"
compose=(docker compose -f compose.yaml -f compose.pg-training.yaml --profile pg-training)
service=pg-training-sync
rollback_needed=0

say() { printf '\n== %s ==\n' "$*"; }
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
rollback() {
  status=$?
  if (( rollback_needed )); then
    printf '\nROLLBACK: stopping only %s; live trading services are untouched.\n' "$service" >&2
    "${compose[@]}" stop "$service" >/dev/null 2>&1 || true
  fi
  exit "$status"
}
trap rollback ERR INT TERM

command -v docker >/dev/null || fail "Docker is required"
docker info >/dev/null 2>&1 || fail "Docker daemon is unavailable"
test -s .secrets/postgres-password || fail "Missing PostgreSQL password secret"
test -f data/ramon_history.sqlite3 || fail "SQLite training source is missing"
test -f compose.pg-training.yaml || fail "Training compose override is missing"
docker image inspect ramon-ai-trader:pg-canary-working >/dev/null 2>&1 || fail "Tested canary image is missing"

say "Validation gate: compose and focused tests"
"${compose[@]}" config --quiet
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync python -m pytest -q \
  tests/test_postgres_training_path.py tests/test_daily_train.py

say "Validation gate: PostgreSQL health"
docker compose --profile logging ps --status running postgres-logs | grep -q postgres-logs \
  || fail "postgres-logs is not running"
docker compose --profile logging exec -T postgres-logs sh -c \
  'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null

say "Start independent training canary"
rollback_needed=1
"${compose[@]}" up -d --no-deps --no-build "$service"

say "Validation gate: successful snapshot log"
for _ in $(seq 1 12); do
  logs=$("${compose[@]}" logs --tail=40 "$service" 2>&1 || true)
  if grep -q 'training snapshot synchronized' <<<"$logs"; then
    rollback_needed=0
    echo "SUCCESS: PostgreSQL training canary is synchronized. SQLite remains authoritative."
    exit 0
  fi
  if grep -q 'training sync failed' <<<"$logs"; then
    fail "Canary synchronization failed"
  fi
  sleep 5
done
fail "Canary did not report a successful snapshot within 60 seconds"
