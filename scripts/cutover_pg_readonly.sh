#!/usr/bin/env bash
# Fast PostgreSQL cutover for read-only services; model and MT5 left untouched.
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
pg=(docker compose -f compose.yaml -f compose.pg-all-staging.yaml)
base=(docker compose -f compose.yaml)
services=(dashboard market-dashboard training-dashboard reversal-daily-report v2-execution-health)
rollback(){
  echo "ROLLBACK: restoring legacy service configuration" >&2
  "${base[@]}" up -d --no-deps --no-build --force-recreate "${services[@]}" || true
}
mode="${1:---check}"
case "$mode" in
  --check)
    "${pg[@]}" config --quiet
    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync python -m pytest -q tests/test_v2_execution_health_pg.py tests/test_training_dashboard_pg_path.py tests/test_postgres_report.py
    echo "PREFLIGHT COMPLETE. No containers changed."; exit 0 ;;
  --apply) ;;
  *) echo "Usage: $0 [--check|--apply]" >&2; exit 64 ;;
esac
# Explicit approval only for dashboard/research services. Does not change trading model.
[[ "${RAMON_APPROVE_READONLY_PG:-}" == YES ]] || { echo "Set RAMON_APPROVE_READONLY_PG=YES" >&2; exit 1; }
"${pg[@]}" config --quiet
trap rollback ERR
for service in "${services[@]}"; do
  echo "Cutover $service"
  "${pg[@]}" up -d --no-deps --no-build --force-recreate "$service"
  id=$("${pg[@]}" ps -q "$service")
  [[ -n "$id" && "$(docker inspect -f '{{.State.Running}}' "$id")" == true ]]
  # Assert the new entrypoint is in the running container.
  docker inspect -f '{{json .Config.Entrypoint}}' "$id" | grep -q pg_service_entrypoint.sh
done
trap - ERR
echo "READ-ONLY SERVICE CUTOVER STARTED; verify dashboard APIs. MODEL/EA UNCHANGED."
