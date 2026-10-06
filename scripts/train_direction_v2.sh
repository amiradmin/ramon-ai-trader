#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "[1/4] Building Ramon image with Direction AI v2 dependencies..."
docker compose --profile tools build model tools

echo "[2/4] Training BUY/SELL Direction AI v2 ensembles..."
docker compose --profile tools run --rm tools \
  -m ramon.direction_roles \
  --db /data/ramon_history.sqlite3 \
  --out /checkpoints/ensemble \
  --symbol XAUUSD_l \
  --chronos-model "${CHRONOS_MODEL:-autogluon/chronos-2-small}"

echo "[3/4] Reloading live role bundle..."
curl -fsS -X POST \
  http://127.0.0.1:8012/reload-roles \
  -H 'Content-Type: application/json' \
  -d '{}' >/tmp/ramon_direction_reload.json

echo "[4/4] Direction AI status:"
curl -fsS http://127.0.0.1:8012/health | python3 -c '
import json,sys
h=json.load(sys.stdin)
keys=[
 "direction_model","direction_schema_version","direction_quality_live",
 "buy_quality_ready","sell_quality_ready","ensemble_mode","ensemble_bundle"
]
for k in keys:
    print(f"{k}: {h.get(k)}")
'

echo
echo "Done. DirectionAI-v2-HistGradientBoosting is live only when the walk-forward promotion gate passes."
