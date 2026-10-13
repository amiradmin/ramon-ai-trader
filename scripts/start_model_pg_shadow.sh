#!/bin/sh
set -eu
password_file=/run/secrets/postgres-password
if [ ! -s "$password_file" ]; then
  echo "PostgreSQL password file missing or empty" >&2
  exit 1
fi
# The DSN is only constructed inside the container, not committed in Compose.
export RAMON_PG_MARKET_SHADOW_DSN="$(PGPASSWORD_FILE="$password_file" python - <<'PY'
import os
from psycopg.conninfo import make_conninfo
with open(os.environ["PGPASSWORD_FILE"], encoding="utf-8") as handle:
    password = handle.read().strip()
if not password:
    raise ValueError("empty PostgreSQL password")
print(make_conninfo(
    host="postgres-logs", port=5432, dbname="ramon_logs",
    user="ramon_logger", password=password, connect_timeout=2,
))
PY
)"
export RAMON_PG_SHADOW_ASYNC_ENABLED=1
export RAMON_M15_WRITE_BACKEND=sqlite
export RAMON_M15_HISTORY_BACKEND=sqlite
exec python -m ramon.server --host 0.0.0.0 --port 8012
