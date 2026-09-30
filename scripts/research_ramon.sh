#!/usr/bin/env bash
set -euo pipefail

# Offline-only: consistent SQLite backup, Entry validation, covariate comparison.
# Build the tools image first. Does not restart the live model or change EA files.
docker compose --profile tools run --rm --no-deps tools -c '
from pathlib import Path
import sqlite3
source = Path("/data/ramon_history.sqlite3")
target = Path("/data/research/input.sqlite3")
target.parent.mkdir(parents=True, exist_ok=True)
with sqlite3.connect(source.as_uri()+"?mode=ro", uri=True) as src:
    with sqlite3.connect(target) as dst:
        src.backup(dst)
print("Consistent research snapshot:", target)
'
docker compose --profile tools run --rm --no-deps tools -m ramon.entry_validation \
  --db /data/research/input.sqlite3 --out /data/research/entry-validation.json
docker compose --profile tools run --rm --no-deps tools -m ramon.research_compare \
  --db /data/research/input.sqlite3 --out /data/research/chronos-covariates.json "$@"
