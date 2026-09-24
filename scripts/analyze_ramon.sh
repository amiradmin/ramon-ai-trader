#!/usr/bin/env bash
set -euo pipefail

# Ramon performance report.
# Usage:
#   bash scripts/analyze_ramon.sh
#   bash scripts/analyze_ramon.sh --all
#   RAMON_REPORT_LIMIT=50 bash scripts/analyze_ramon.sh
#
# Optional env:
#   RAMON_HISTORY_DB=/data/ramon_history.sqlite3
#   RAMON_SYMBOL=XAUUSD_l
#   RAMON_REPORT_LIMIT=20

DB="${RAMON_HISTORY_DB:-/data/ramon_history.sqlite3}"
SYMBOL="${RAMON_SYMBOL:-XAUUSD_l}"
LIMIT="${RAMON_REPORT_LIMIT:-20}"

if [[ "${1:-}" == "--all" ]]; then
  LIMIT=0
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker is not installed or not in PATH." >&2
  exit 1
fi

if ! docker compose ps --status running --services 2>/dev/null | grep -qx "model"; then
  echo "ERROR: docker compose service 'model' is not running." >&2
  echo "Start it with: docker compose up -d model" >&2
  exit 1
fi

docker compose exec -T \
  -e RAMON_REPORT_DB="$DB" \
  -e RAMON_REPORT_SYMBOL="$SYMBOL" \
  -e RAMON_REPORT_LIMIT="$LIMIT" \
  model python -m ramon.report

# EA-side sizing telemetry (including min_lot_override_used) is historical CSV
# evidence and is not persisted in SQLite. Add a strict, read-only candidate
# comparison when that CSV is available on the host.
CSV="${RAMON_SIGNAL_CSV:-}"
if [[ -n "$CSV" && ! -f "$CSV" ]]; then
  echo "WARNING: RAMON_SIGNAL_CSV does not point to a file: $CSV" >&2
  CSV=""
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

echo
if [[ -n "$CSV" ]]; then
  echo "Sizing CSV: $CSV (strict candidate matching; verify this is the intended terminal)"
  docker compose exec -T \
    -e RAMON_REPORT_DB="$DB" \
    -e RAMON_REPORT_SYMBOL="$SYMBOL" \
    model python -m ramon.sizing_audit --signal-csv - < "$CSV"
else
  echo "=== MIN-LOT OVERRIDE PERFORMANCE ==="
  echo "Ramon_Signals.csv not found; override performance is UNKNOWN."
fi
