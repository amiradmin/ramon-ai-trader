#!/usr/bin/env bash
# Read-only demo signal connectivity audit. No deploy, order, restart, or config changes.
set -u
echo "=== ACCOUNT/EA CHECK ==="
echo "Confirm terminal: LiteFinance MT5 DEMO / XAUUSD_o M15"
echo "=== MODEL HEALTH ==="
curl --max-time 5 -sS -o /tmp/ramon-demo-health-$$.json -w 'HTTP %{http_code}\n' http://127.0.0.1:8012/health
if [[ -f /tmp/ramon-demo-health-$$.json ]]; then
  python3 -m json.tool /tmp/ramon-demo-health-$$.json 2>/dev/null | head -45
  rm -f /tmp/ramon-demo-health-$$.json
fi
echo "=== MODEL DECISION REQUESTS / ERRORS (last 15m) ==="
docker logs --since 15m ramon-ai-trader-model-1 2>&1 | grep -E 'POST /decision|Ramon decision failure|Ramon history persistence warning| 400 | 503 ' | tail -30 || true
echo "=== MT5 EXPERTS (today; latest Ramon messages) ==="
log="$HOME/.mt5/drive_c/Program Files/MetaTrader 5/MQL5/logs/$(date +%Y%m%d).log"
if [[ -f "$log" ]]; then
 python3 - "$log" <<'PY'
import sys
from pathlib import Path
raw=Path(sys.argv[1]).read_bytes()
encoding="utf-16" if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else "utf-16-le" if b'\x00' in raw[:128] else "utf-8-sig"
lines=raw.decode(encoding,errors="replace").splitlines()
match=[line for line in lines if "Ramon (" in line or "Ramon\t" in line]
for line in match[-35:]:
 print(line)
PY
else
 echo "Experts log not found: $log"
fi
echo "=== COLLECTOR files ==="
find "$HOME/.mt5" -type f \( -name 'RamonLive_XAUUSD_o_M1.csv' -o -name 'RamonLive_XAUUSD_o_M5.csv' \) -printf '%TY-%Tm-%Td %TH:%TM %s %p\n' 2>/dev/null | head -10
