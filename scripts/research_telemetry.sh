#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose --profile tools run --rm --no-deps \
  -v "$PWD/src:/research-src:ro" -e PYTHONPATH=/research-src \
  tools -m ramon.telemetry "$@"
