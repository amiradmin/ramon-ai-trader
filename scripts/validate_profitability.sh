#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ $# -ne 1 ]]; then
  echo 'Usage: bash scripts/validate_profitability.sh YYYY-MM-DDTHH:MM:SSZ' >&2
  echo 'Use the fixed UTC time when BOTH v0.60 charts began the forward test.' >&2
  exit 2
fi
docker compose --profile tools run --rm --no-deps tools \
  -m ramon.profitability --db /data/ramon_history.sqlite3 \
  --since-utc "$1" --version 0.60 --capital-usd 30
docker compose --profile tools run --rm --no-deps tools \
  -m ramon.exit_research --db /data/ramon_history.sqlite3
