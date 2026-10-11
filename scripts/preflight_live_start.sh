#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
DIAG="${RAMON_DIAGNOSTIC_FILE:-$HOME/.mt5/drive_c/users/$USER/AppData/Roaming/MetaQuotes/Terminal/Common/Files/Ramon_Diagnostic.txt}"

cd "$ROOT"
docker compose ps
.venv/bin/python -m ramon.live_start_preflight --diagnostic "$DIAG"
