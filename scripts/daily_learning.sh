#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

mkdir -p data checkpoints/daily checkpoints/ensemble
ACTIVE="checkpoints/active_model.txt"
BEFORE="$(cat "$ACTIVE" 2>/dev/null || true)"

STEPS="${RAMON_DAILY_STEPS:-100}"
MIN_TRADES="${RAMON_DAILY_MIN_TRADES:-20}"
MIN_IMPROVEMENT="${RAMON_DAILY_MIN_IMPROVEMENT_R:-0.5}"
MAX_DD="${RAMON_DAILY_MAX_DRAWDOWN_R:-8.0}"

echo "=== CHRONOS DAILY CHALLENGER ==="
docker compose --profile tools run --rm tools   -m ramon.daily_train   --db /data/ramon_history.sqlite3   --device cpu   --steps "$STEPS"   --minimum-trades "$MIN_TRADES"   --minimum-improvement-r "$MIN_IMPROVEMENT"   --maximum-drawdown-r "$MAX_DD"   --auto-promote

echo "=== RAMON ROLE MODELS ==="
docker compose --profile tools run --rm tools   -m ramon.train_roles   --db /data/ramon_history.sqlite3   --out /checkpoints/ensemble

AFTER="$(cat "$ACTIVE" 2>/dev/null || true)"
if [[ -n "$AFTER" && "$AFTER" != "$BEFORE" ]]; then
  echo "New Chronos model promoted: $AFTER"
else
  echo "No Chronos promotion."
fi

echo "Reloading live service so promoted Chronos/role models are picked up."
docker compose up -d --force-recreate model
sleep 3
curl -fsS http://127.0.0.1:8012/health
echo
