#!/usr/bin/env bash
set -euo pipefail

DB="${RAMON_HISTORY_DB:-/data/ramon_history.sqlite3}"
SYMBOL="${RAMON_SYMBOL:-XAUUSD_l}"

if command -v uv >/dev/null 2>&1; then
  exec uv run python -m ramon.volatility_regime_lab --db "$DB" --symbol "$SYMBOL" "$@"
fi

exec python -m ramon.volatility_regime_lab --db "$DB" --symbol "$SYMBOL" "$@"
