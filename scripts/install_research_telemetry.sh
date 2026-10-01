#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
# Backup committed database + WAL before any schema migration.
backup_name="/data/backups/pre-telemetry-$(date -u +%Y%m%dT%H%M%SZ).sqlite3"
bash scripts/research_telemetry.sh backup --out "$backup_name"
# Compile using the user's existing MetaEditor; fail before restarting service
# if it does not produce a fresh EX5. Do not change stored chart inputs.
bash scripts/setup_local.sh --mt5-only
docker compose -f compose.yaml -f compose.telemetry.yaml up -d --no-deps --no-build --force-recreate model
printf '%s\n' 'Telemetry service installed. Wait for /health, then reload Ramon MAIN/SMALL and confirm EA 0.53.6.'
printf '%s\n' 'Report: bash scripts/research_telemetry.sh report'
