#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
mkdir -p data checkpoints/daily checkpoints/ensemble
# One writer owns bundle staging and promotion across the entire daily job.
exec 9>checkpoints/daily_learning.lock
flock -n 9 || { echo "Daily learning is already running."; exit 0; }
ACTIVE="checkpoints/active_model.txt"
ROLES="checkpoints/ensemble/active.json"
BEFORE="$(cat "$ACTIVE" 2>/dev/null || true)"
ROLES_BEFORE="$(cat "$ROLES" 2>/dev/null || true)"
STEPS="${RAMON_DAILY_STEPS:-100}"
MIN_TRADES="${RAMON_DAILY_MIN_TRADES:-20}"
MIN_IMPROVEMENT="${RAMON_DAILY_MIN_IMPROVEMENT_R:-0.5}"
MAX_DD="${RAMON_DAILY_MAX_DRAWDOWN_R:-8.0}"

# A role bundle is pinned to the Chronos checkpoint that generated its features.
# Keep that checkpoint fixed while collecting its real trade labels too.
# Chronos challengers are trained/evaluated, but require a new compatible role cohort.
PROMOTION=()
if ! docker compose --profile tools run --rm tools -m ramon.daily_train \
  --db /data/ramon_history.sqlite3 --device cpu --steps "$STEPS" \
  --base "${CHRONOS_MODEL:-autogluon/chronos-2-small}" \
  --minimum-trades "$MIN_TRADES" --minimum-improvement-r "$MIN_IMPROVEMENT" \
  --maximum-drawdown-r "$MAX_DD" "${PROMOTION[@]}"; then
  echo "Chronos challenger failed; continuing independent role training."
fi

if ! docker compose --profile tools run --rm tools -m ramon.train_roles \
  --db /data/ramon_history.sqlite3 --out /checkpoints/ensemble \
  --chronos-model "${CHRONOS_MODEL:-autogluon/chronos-2-small}"; then
  echo "Role training failed; existing active bundle remains selected."
fi

AFTER="$(cat "$ACTIVE" 2>/dev/null || true)"
ROLES_AFTER="$(cat "$ROLES" 2>/dev/null || true)"
if [[ "$AFTER" != "$BEFORE" || "$ROLES_AFTER" != "$ROLES_BEFORE" ]]; then
  docker compose up -d --force-recreate model
  # Wait for actual readiness, including model warmup; do not assume a 3s startup.
  docker compose exec -T model python -c '
import time, urllib.request
for attempt in range(90):
    try:
        print(urllib.request.urlopen("http://127.0.0.1:8012/health", timeout=3).read().decode())
        break
    except OSError:
        time.sleep(2)
else:
    raise SystemExit("Model did not become healthy after promotion")
'
else
  echo "No promotion; live service was not restarted."
fi
