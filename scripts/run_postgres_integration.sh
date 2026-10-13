#!/usr/bin/env bash
# Runs only the isolated test Compose project; no live Ramon compose file is used.
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
dc=(docker compose -f compose.postgres-test.yaml)
cleanup(){ "${dc[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true; }
trap cleanup EXIT
"${dc[@]}" up -d --wait
export RAMON_PG_DSN='postgresql://ramon_test:ramon_test_only@127.0.0.1:55432/ramon_test'
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync python -m pytest -q tests/test_postgres_training_path.py tests/test_postgres_history.py
