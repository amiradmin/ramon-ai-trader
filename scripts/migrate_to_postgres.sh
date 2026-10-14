#!/usr/bin/env bash
# Controlled SQLite -> PostgreSQL migration gate.  Never restarts Ramon/MT5/EA.
# Default is a read-only preflight; --apply performs only additive backup/archive/
# reconciliation work and still refuses service cutover or SQLite deletion.
set -Eeuo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"
apply=0
sqlite=${RAMON_HISTORY_DB:-data/ramon_history.sqlite3}
container=${RAMON_PG_CONTAINER:-ramon-ai-trader-postgres-logs-1}
backup_dir=${RAMON_MIGRATION_BACKUP_DIR:-$root/backups/postgres-migration-$(date -u +%Y%m%dT%H%M%SZ)}

while (($#)); do
  case "$1" in
    --apply) apply=1 ;;
    --sqlite) sqlite=$2; shift ;;
    --container) container=$2; shift ;;
    *) echo "Usage: $0 [--apply] [--sqlite PATH] [--container NAME]" >&2; exit 64 ;;
  esac
  shift
done
fail() { echo "MIGRATION BLOCKED: $*" >&2; exit 1; }
say() { printf '\n== %s ==\n' "$*"; }
test -f "$sqlite" || fail "SQLite source not found"
command -v docker >/dev/null || fail "Docker is unavailable"
docker info >/dev/null 2>&1 || fail "Docker daemon is unavailable"
docker inspect "$container" >/dev/null 2>&1 || fail "PostgreSQL container not found"
test -s .secrets/postgres-password || fail "PostgreSQL secret is unavailable"

say "Read-only configuration and regression gates"
docker compose -f compose.yaml config --quiet
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync python -m pytest -q \
  tests/test_postgres_training_path.py tests/test_postgres_history.py \
  tests/test_postgres_m15_history_backend.py tests/test_postgres_m15_reconcile.py
uv run --no-sync python scripts/postgres_archive_sqlite.py --sqlite "$sqlite" --container "$container"
uv run --no-sync python scripts/reconcile_postgres_m15.py --sqlite "$sqlite"

if (( ! apply )); then
  echo "PREFLIGHT PASSED: no data or service configuration was changed. Re-run with --apply for additive backup/archive/reconciliation only."
  exit 0
fi

say "Create recoverable backups"
mkdir -p "$backup_dir"
cp --reflink=auto --preserve=mode,timestamps "$sqlite" "$backup_dir/$(basename "$sqlite")"
sha256sum "$sqlite" "$backup_dir/$(basename "$sqlite")" > "$backup_dir/sqlite.sha256"
docker exec "$container" sh -c 'exec pg_dump -Fc -U "$POSTGRES_USER" -d "$POSTGRES_DB"' > "$backup_dir/postgres.before.dump"
test -s "$backup_dir/postgres.before.dump" || fail "PostgreSQL backup is empty"

say "Additive archive and typed M15 reconciliation"
uv run --no-sync python scripts/postgres_archive_sqlite.py --sqlite "$sqlite" --container "$container" --execute
test -n "${RAMON_PG_HISTORY_DSN:-}" || fail "RAMON_PG_HISTORY_DSN must be supplied from the host secret manager for typed M15 reconciliation"
uv run --no-sync python scripts/reconcile_postgres_m15.py --sqlite "$sqlite" --dsn "$RAMON_PG_HISTORY_DSN" --execute

say "Parity and deletion gate"
set +e
uv run --no-sync python scripts/postgres_parity_audit.py --sqlite "$sqlite" --container "$container" --json > "$backup_dir/parity.json"
audit_status=$?
set -e
test "$audit_status" -eq 2 || fail "Parity audit failed; backups retained and no service was changed"
echo "MIGRATION DATA PHASE COMPLETE: archive/reconciliation succeeded. SQLite deletion and service cutover remain explicitly blocked."
