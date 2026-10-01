#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
# Independent tools process; current source, no image rebuild or live restart.
# Limit this research worker on small machines and keep model BLAS threads at one.
docker compose -f compose.yaml -f compose.forward-research.yaml \
  --profile tools run --rm --no-deps \
  -v "$PWD/src:/research-src:ro" -e PYTHONPATH=/research-src \
  -e OMP_NUM_THREADS=1 -e MKL_NUM_THREADS=1 -e OPENBLAS_NUM_THREADS=1 \
  tools -m ramon.forward_research "$@"
