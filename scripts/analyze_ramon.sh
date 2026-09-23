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
