#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

SOURCE="${1:-$HOME/.mt5/drive_c/users/$USER/AppData/Roaming/MetaQuotes/Terminal/Common/Files/Ramon_XAUUSD_l_M15_History.csv}"
SYMBOL="${RAMON_SYMBOL:-XAUUSD_l}"
DEST_DIR="data/imports"
DEST="$DEST_DIR/$(basename "$SOURCE")"

if [[ ! -f "$SOURCE" ]]; then
  echo "History CSV not found: $SOURCE" >&2
  echo "Run mt5/ExportRamonHistory.mq5 in MetaTrader 5 first." >&2
  exit 2
fi

mkdir -p "$DEST_DIR"
cp -f "$SOURCE" "$DEST"

echo "=== IMPORT MT5 HISTORY ==="
docker compose run --rm tools   -m ramon.import_history "/data/imports/$(basename "$DEST")"   --symbol "$SYMBOL"

echo
echo "=== BACKFILL TARGET OUTCOMES ==="
docker compose run --rm tools   -m ramon.target_outcomes   --symbol "$SYMBOL"

echo
echo "=== DATASET STATUS ==="
docker compose run --rm tools - <<'PY'
import sqlite3

db="/data/ramon_history.sqlite3"
con=sqlite3.connect(db)
print("M15 bars:", con.execute(
    "SELECT COUNT(*) FROM history_bars WHERE symbol='XAUUSD_l' AND timeframe='M15'"
).fetchone()[0])
print("schema v4 samples:", con.execute(
    "SELECT COUNT(*) FROM decision_samples WHERE schema_version>=4"
).fetchone()[0])
print("target outcomes:", con.execute(
    "SELECT COUNT(*) FROM target_outcomes"
).fetchone()[0])
print("TP1 hits:", con.execute(
    "SELECT COALESCE(SUM(tp1_hit),0) FROM target_outcomes"
).fetchone()[0])
print("TP2 continuations:", con.execute(
    "SELECT COALESCE(SUM(continuation_tp2),0) FROM target_outcomes"
).fetchone()[0])
print("TP3 continuations:", con.execute(
    "SELECT COALESCE(SUM(continuation_tp3),0) FROM target_outcomes"
).fetchone()[0])
PY
