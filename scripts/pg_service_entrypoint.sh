#!/bin/sh
# Source-only PostgreSQL connection initialization. Never prints secrets.
set -eu
test -s /run/secrets/postgres-password || { echo "PostgreSQL password missing" >&2; exit 1; }
export RAMON_PG_DSN="$(python - <<'PY'
from psycopg.conninfo import make_conninfo
from pathlib import Path
print(make_conninfo(host="postgres-logs", port=5432,
                    dbname="ramon_logs", user="ramon_logger",
                    password=Path("/run/secrets/postgres-password").read_text().strip(),
                    connect_timeout=3))
PY
)"
export RAMON_PG_TRAINING_DSN="$RAMON_PG_DSN"
export RAMON_STORAGE_BACKEND=postgres
export RAMON_TRAINING_BACKEND=postgres
exec "$@"
