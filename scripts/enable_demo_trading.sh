#!/usr/bin/env bash
# Turn the DEMO command gates on/off in the local, git-ignored .env and recreate only Django.
# Usage: scripts/enable_demo_trading.sh on|off [expanded|standard]
# This never touches the EA's own EnableCommandExecution input or the server's demo-account check:
# Django still refuses to create commands unless MT5 telemetry reports account_trade_mode=0 (demo).
set -euo pipefail
cd "$(dirname "$0")/.."
mode="${1:-}"; profile="${2:-expanded}"
case "$mode" in on) value=1 ;; off) value=0 ;; *) echo "usage: $0 on|off [expanded|standard]" >&2; exit 2 ;; esac
case "$profile" in expanded|standard) ;; *) echo "profile must be expanded or standard" >&2; exit 2 ;; esac
[ -f .env ] || { echo ".env not found; create it first (see README)" >&2; exit 1; }
set_var() {
  if grep -q "^$1=" .env; then sed -i "s|^$1=.*|$1=$2|" .env; else printf '%s=%s\n' "$1" "$2" >> .env; fi
}
set_var RAMON_EA_COMMAND_CREATION_ENABLED "$value"
set_var RAMON_EA_COMMAND_EXECUTION_ENABLED "$value"
set_var RAMON_DEMO_PROFILE "$profile"
docker compose up -d --force-recreate django
echo "Demo command gates: $mode (profile: $profile). Also set EnableCommandExecution=true in the MT5 EA inputs on the demo terminal."
