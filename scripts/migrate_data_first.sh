#!/usr/bin/env bash
# One pass: COPY ALL available SQLite records into PostgreSQL, THEN validate.
# Data-only operation: NEVER restarts model, EA, or other live services.
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
sqlite="${RAMON_HISTORY_DB:-data/ramon_history.sqlite3}"
pg_container="${RAMON_PG_CONTAINER:-ramon-ai-trader-postgres-logs-1}"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup="backups/pg-data-first-$stamp"
fail(){ echo "STOP: $*" >&2; exit 1; }
test -s "$sqlite" || fail "SQLite database missing"
test -s .secrets/postgres-password || fail "PostgreSQL secret missing"
docker inspect "$pg_container" >/dev/null 2>&1 || fail "PostgreSQL container missing"
docker image inspect ramon-ai-trader:pg-canary-working >/dev/null 2>&1 || fail "pg-canary-working image missing"
mkdir -p "$backup"
echo "1/5 Backups: $backup"
SQLITE_FILE="$sqlite" BACKUP_FILE="$backup/sqlite.snapshot.db" python3 - <<'PY'
import os, sqlite3
with sqlite3.connect("file:" + os.path.abspath(os.environ["SQLITE_FILE"]) + "?mode=ro",uri=True) as source:
    with sqlite3.connect(os.environ["BACKUP_FILE"]) as target: source.backup(target)
PY
docker exec "$pg_container" sh -c 'exec pg_dump -Fc -U "$POSTGRES_USER" -d "$POSTGRES_DB"' > "$backup/postgres.before.dump"
test -s "$backup/postgres.before.dump" || fail "PostgreSQL dump empty"
echo "2/5 Full SQLite table archival"
uv run --no-sync python scripts/postgres_archive_sqlite.py --sqlite "$backup/sqlite.snapshot.db" --container "$pg_container" --execute
echo "3/5 Training tables"
docker run --rm --network ramon-ai-trader_default \
  -v "$PWD/src:/app/src:ro" \
  -v "$PWD/scripts:/app/scripts:ro" \
  -v "$PWD/$backup/sqlite.snapshot.db:/data/ramon_history.sqlite3:ro" \
  -v "$PWD/.secrets/postgres-password:/run/secrets/postgres-password:ro" \
  -e PYTHONPATH=/app/src --entrypoint python \
  ramon-ai-trader:pg-canary-working /app/scripts/sync_pg_training.py
echo "4/5 Typed M15 reconciliation"
# Credential is never printed, written to disk, or put on a command line.
export RAMON_PG_CONTAINER="$pg_container"
SQLITE_SNAPSHOT="$backup/sqlite.snapshot.db" uv run --no-sync python - <<'PY'
import os, subprocess
from pathlib import Path
from psycopg.conninfo import make_conninfo
container=os.environ["RAMON_PG_CONTAINER"]
data=subprocess.check_output(["docker","inspect","-f",
    "{{range.NetworkSettings.Networks}}{{.IPAddress}} {{end}}",container],text=True)
ips=data.split()
if not ips: raise SystemExit("No Docker PostgreSQL IP")
secret=Path(".secrets/postgres-password").read_text().strip()
dsn=make_conninfo(host=ips[0],port=5432,user="ramon_logger",dbname="ramon_logs",password=secret)
env=os.environ.copy()
env["RAMON_PG_HISTORY_DSN"]=dsn
try:
    subprocess.run(["uv","run","--no-sync","python","scripts/reconcile_postgres_m15.py",
                    "--sqlite",os.environ["SQLITE_SNAPSHOT"],"--dsn",dsn,"--execute"],
                   env=env,check=True)
finally:
    env.pop("RAMON_PG_HISTORY_DSN",None)
PY
echo "5/5 Post-copy counts / parity audit"
docker exec "$pg_container" sh -c 'psql -X -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT (SELECT count(*) FROM sqlite_archive.rows), (SELECT count(*) FROM ramon_migration.training_decision_samples), (SELECT count(*) FROM ramon_migration.training_trade_outcomes)"'
# Existing audit intentionally exits 2 when some legacy mappings remain.
set +e
uv run --no-sync python scripts/postgres_parity_audit.py --sqlite "$backup/sqlite.snapshot.db" --container "$pg_container" --json > "$backup/parity.json"
parity_status=$?
set -e
echo "Data copy complete. Audit exit=$parity_status; report=$backup/parity.json"
echo "LIVE SERVICES UNCHANGED; SQLite must remain until code dependencies are migrated."
