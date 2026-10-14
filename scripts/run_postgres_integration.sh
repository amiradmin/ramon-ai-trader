#!/usr/bin/env bash
# Runs only the isolated test Compose project; no live Ramon compose file is used.
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
dc=(docker compose -f compose.postgres-test.yaml)
cleanup(){ "${dc[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true; }
trap cleanup EXIT
"${dc[@]}" up -d --wait
# The host-side test process may be network-namespaced (as it is in Codex),
# where a published loopback port is unavailable.  Resolve the isolated
# container address instead; this never points at a live PostgreSQL service.
test_id="$("${dc[@]}" ps -q postgres-test)"
test_host="$(docker inspect -f '{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$test_id")"
test -n "$test_host"
export RAMON_PG_DSN="postgresql://ramon_test:ramon_test_only@${test_host}:5432/ramon_test"
export RAMON_PG_TEST_DSN="$RAMON_PG_DSN"
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync python -m pytest -q \
  tests/test_postgres_training_path.py tests/test_postgres_history.py \
  tests/test_postgres_operational_integration.py \
  tests/test_server_postgres_manual_paths.py tests/test_mtf_collector.py \
  tests/test_training_dashboard_pg_path.py tests/test_postgres_report.py
