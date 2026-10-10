#!/usr/bin/env bash
# Non-trading readiness checks for Ramon V2 before the next XAUUSD session.
# Never starts MT5, enables AutoTrading, compiles MQL5, or sends an order.
set -uo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
errors=0
warns=0
ok(){ printf '[PASS] %s\n' "$*"; }
fail(){ printf '[FAIL] %s\n' "$*"; errors=$((errors+1)); }
warn(){ printf '[WARN] %s\n' "$*"; warns=$((warns+1)); }
echo "=== Ramon V2 read-only market preflight ==="
echo "UTC: $(date -u +'%Y-%m-%d %H:%M:%S UTC')"
if [[ -n "$(git status --porcelain 2>/dev/null)" ]]; then warn "Uncommitted changes present: leave them untouched"; else ok "Git worktree clean"; fi
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  ok "Docker Compose available"
  if docker compose config -q; then ok "Compose syntax valid"; else fail "Compose invalid"; fi
  if docker compose ps --status running --services 2>/dev/null | grep -Fxq model; then
    ok "Model container running"
  else fail "Model container is not running"; fi
  if docker compose ps --status running --services 2>/dev/null | grep -Fxq v2-execution-health; then
    ok "Execution-health worker running"
  else warn "Execution-health worker not running"; fi
else
  fail "Docker Compose unavailable"
fi
if command -v curl >/dev/null 2>&1; then
  payload="$(curl --silent --show-error --max-time 8 http://127.0.0.1:8012/health 2>/dev/null)" || payload=""
  if [[ -n "$payload" ]] && python3 -c 'import json,sys; h=json.load(sys.stdin); assert h.get("ready") is True; assert h.get("ai_engine_v2_enabled") is not False' <<<"$payload" 2>/dev/null; then
    ok "Model /health ready (V2 not explicitly disabled)"
  else fail "Model /health not ready or V2 disabled"; fi
else fail "curl unavailable"; fi
if [[ -f data/v2_mt5_execution_health.json ]]; then
  if python3 - <<'PY'
import json,time
from pathlib import Path
p=Path("data/v2_mt5_execution_health.json")
x=json.loads(p.read_text())
assert x.get("ready") is True
assert x.get("live_order_access") is False
assert x.get("symbol")=="XAUUSD_l"
assert time.time()-p.stat().st_mtime < 180
PY
  then ok "Fresh read-only V2 execution report"; else warn "V2 execution report stale or invalid"; fi
else warn "V2 execution report not found"; fi
if [[ -f mt5/Ramon.mq5 ]]; then
  if grep -q 'bool V2Preflight(' mt5/Ramon.mq5; then ok "Account-risk guard exists in MQL5 source"; else fail "Account-risk guard absent"; fi
else fail "EA source not found"; fi
if [[ -x .venv/bin/python ]]; then
  if PYTHONPATH=src .venv/bin/python -m pytest -q \
      tests/test_v2_account_risk.py \
      tests/test_v2_mt5_ea_wiring.py \
      tests/test_v2_mt5_execution_health.py \
      tests/test_v3_mt5_integrations.py; then ok "Risk + integration Python tests"; else fail "Python tests failed"; fi
else warn "No .venv Python found; run uv sync --extra dev then repeat"; fi
echo
echo "=== Broker-terminal checks REQUIRED (not automated here) ==="
echo "1. Compile exactly mt5/Ramon.mq5 with MetaEditor: zero errors."
echo "2. Confirm the .ex5 in the correct active MT5 Experts data folder."
echo "3. Open XAUUSD_l M15, same broker account, fresh quotes and connected server."
echo "4. EnableLiveTrading stays FALSE until operator separately approves DEMO results."
echo "5. Confirm MoneyUnitsPerUSD, portfolio cap, free margin, no Stop Out latch."
echo "6. Test BOTH standard and Guardian entry guards in Strategy Tester / demo."
echo "7. Only one authorized EA controls entries; don't duplicate the chart."
echo
echo "Automated checks: $errors failure(s), $warns warning(s)"
if ((errors>0)); then exit 1; fi
echo "Automated subset passed. NOT authorization for live trading."
