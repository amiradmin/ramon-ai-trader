#!/usr/bin/env bash
set -euo pipefail
ramon_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ramon_root"
ramon_python="$ramon_root/.venv/bin/python"
if [[ ! -x "$ramon_python" ]]; then ramon_python=python3; fi
export PYTHONPATH="$ramon_root/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$ramon_python" -m ramon.monitor "$@"
