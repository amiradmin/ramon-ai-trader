#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "=== DAILY LEARNING SCHEDULER ==="
if command -v systemctl >/dev/null 2>&1; then
  systemctl --user show ramon-daily-learning.timer \
    -p LoadState -p ActiveState -p NextElapseUSecRealtime -p LastTriggerUSec || true
  systemctl --user show ramon-daily-learning.service \
    -p LoadState -p ActiveState -p Result -p ExecMainStatus || true
else
  echo "systemctl unavailable; scheduler status UNKNOWN"
fi

# Host CSV is optional. Pass it on stdin, without copying it into the live data directory.
CSV="${RAMON_SIGNAL_CSV:-}"
if [[ -n "$CSV" && ! -f "$CSV" ]]; then
  echo "ERROR: RAMON_SIGNAL_CSV does not point to a file: $CSV" >&2
  exit 1
fi
if [[ -z "$CSV" ]]; then
  for prefix in "${RAMON_WINEPREFIX:-}" "$HOME/.mt5" "$HOME/.wine"; do
    [[ -n "$prefix" && -d "$prefix/drive_c/users" ]] || continue
    while IFS= read -r -d '' candidate; do
      if [[ -z "$CSV" || "$candidate" -nt "$CSV" ]]; then CSV="$candidate"; fi
    done < <(find "$prefix/drive_c/users" -type f \
      -path '*/MetaQuotes/Terminal/Common/Files/Ramon_Signals.csv' -print0 2>/dev/null)
  done
fi
if [[ -n "$CSV" ]]; then
  echo "Optional signal CSV: $CSV (verify this is the intended terminal)"
  docker compose exec -T model python -m ramon.audit \
    --health-url http://127.0.0.1:8012/health --signal-csv - "$@" < "$CSV"
else
  docker compose exec -T model python -m ramon.audit \
    --health-url http://127.0.0.1:8012/health "$@"
fi
