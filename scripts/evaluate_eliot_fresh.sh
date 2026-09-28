#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

source_csv="${1:-$HOME/.mt5/drive_c/users/$USER/AppData/Roaming/MetaQuotes/Terminal/Common/Files/Ramon_XAUUSD_l_M5_History.csv}"
manifest="data/eliot_forward/manifest.json"
db="data/ramon_history.sqlite3"

if [[ ! -s "$source_csv" ]]; then
  echo "Missing M5 export: $source_csv" >&2
  echo "Run ExportRamonHistory with PERIOD_M5 in MT5 first." >&2
  exit 2
fi
if [[ ! -s "$manifest" ]]; then
  echo "Missing frozen Eliot model: $manifest" >&2
  exit 2
fi

python3 - "$source_csv" "$manifest" "$db" <<'PY'
import csv
import json
from pathlib import Path
import sqlite3
import sys

source, manifest, db = map(Path, sys.argv[1:])
with source.open(newline="") as stream:
    reader = csv.DictReader(stream)
    if not reader.fieldnames or "time" not in reader.fieldnames:
        sys.exit("M5 export has no time column")
    last = None
    for row in reader:
        last = int(row["time"])
if last is None:
    sys.exit("M5 export contains no completed bars")

frozen = json.loads(manifest.read_text(encoding="utf-8"))
cutoff = int(frozen["cutoff_time"])
if frozen["symbol"] != "XAUUSD_l" or frozen["units_per_price"] != 1:
    sys.exit("Frozen Eliot model does not match XAUUSD_l and units-per-price=1")
if last <= cutoff:
    sys.exit(f"No bars after frozen cutoff: latest={last}, cutoff={cutoff}")

if db.exists():
    with sqlite3.connect(db) as connection:
        latest_imported = connection.execute(
            "SELECT MAX(time) FROM history_bars WHERE symbol=? AND timeframe='M5'",
            ("XAUUSD_l",),
        ).fetchone()[0]
    if latest_imported is not None and last <= latest_imported:
        sys.exit(f"No new M5 bars since last import: latest={last}")

print(f"New M5 export: latest={last}, frozen cutoff={cutoff}", flush=True)
PY

mkdir -p data/imports
cp -- "$source_csv" data/imports/Ramon_XAUUSD_l_M5_History.csv

docker compose --profile tools run --rm --no-deps tools \
  -m ramon.import_history \
  /data/imports/Ramon_XAUUSD_l_M5_History.csv \
  --db /data/ramon_history.sqlite3 --symbol XAUUSD_l --timeframe M5

docker compose --profile tools run --rm --no-deps tools \
  -m ramon.eliot_forward evaluate \
  --db /data/ramon_history.sqlite3 --symbol XAUUSD_l \
  --model autogluon/chronos-2-small --device cpu --units-per-price 1
