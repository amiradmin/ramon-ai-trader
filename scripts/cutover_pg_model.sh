#!/usr/bin/env bash
# Opt-in model cutover. Requires independent human verification of *all* MT5 open positions.
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
base=(docker compose -f compose.yaml)
pg=(docker compose -f compose.yaml -f compose.pg-all-staging.yaml)
if [[ "${1:---check}" != --check && "${1}" != --apply ]]; then echo "Usage: $0 --check|--apply"; exit 64; fi
"${pg[@]}" config --quiet
test -s .secrets/postgres-password
docker image inspect ramon-ai-trader:pg-canary-working >/dev/null
echo "Baseline health:"
curl -fsS --max-time 10 http://127.0.0.1:8012/health | python3 -c 'import json,sys; x=json.load(sys.stdin); assert x.get("ready") is True; print("ready:",x["ready"],"history_last_error:",x.get("history_last_error"))'
if [[ "${1:---check}" == --check ]]; then
  echo "PREFLIGHT PASS. No changes made. Before --apply confirm no open MT5 positions, including manual trades."
  exit 0
fi
[[ "${RAMON_CONFIRMED_NO_OPEN_MT5_POSITIONS:-}" == YES ]] || { echo "STOP: verify MT5 Trade tab is empty, then set RAMON_CONFIRMED_NO_OPEN_MT5_POSITIONS=YES" >&2; exit 1; }
rollback(){
  echo "Model health failed: restoring legacy Compose configuration" >&2
  "${base[@]}" up -d --no-deps --no-build --force-recreate model || true
}
trap rollback ERR
"${pg[@]}" up -d --no-deps --no-build --force-recreate model
id=$("${pg[@]}" ps -q model)
[[ -n "$id" ]]
docker inspect -f '{{json .Config.Entrypoint}}' "$id" | grep -q pg_service_entrypoint.sh
for i in $(seq 1 60); do
  if curl -fsS --max-time 3 http://127.0.0.1:8012/health 2>/dev/null | python3 -c 'import json,sys; x=json.load(sys.stdin); assert x.get("ready") is True and not x.get("history_last_error")' 2>/dev/null; then
    echo "MODEL PG HEALTHY; check live decision persistence and MT5 diagnostics before retiring SQLite."
    trap - ERR
    exit 0
  fi
  if [[ "$(docker inspect -f '{{.State.Running}}' "$id" 2>/dev/null || true)" != true ]]; then break; fi
  sleep 5
done
echo "MODEL PG NOT HEALTHY" >&2
exit 1
