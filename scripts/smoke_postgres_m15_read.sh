#!/usr/bin/env bash
# Read-only comparison using a fresh ephemeral Ramon container and the running PostgreSQL.
set -euo pipefail
cd "$(dirname "$0")/.."
image="${RAMON_SMOKE_IMAGE:-ramon-ai-trader:build-test}"
db_container="$(docker compose ps -q postgres-logs)"
[[ -n "$db_container" ]] || { echo "postgres-logs is not running" >&2; exit 2; }
docker image inspect "$image" >/dev/null
# Keep the password out of argv and the terminal. This uses a short-lived private mount.
secret="$(mktemp)"
chmod 600 "$secret"
trap 'rm -f "$secret"' EXIT
docker exec "$db_container" sh -c 'printf %s "$POSTGRES_PASSWORD"' > "$secret"
[[ -s "$secret" ]] || { echo "Database credential unavailable" >&2; exit 2; }
docker run --rm --network ramon-ai-trader_default \
  --mount "type=bind,src=$PWD/data,dst=/run/ramon_data,readonly" \
  --mount "type=bind,src=$secret,dst=/run/pg_password,readonly" \
  --entrypoint python "$image" -c '
import json, os, sqlite3
import psycopg
from psycopg.conninfo import make_conninfo
from ramon.postgres_m15_history import load_m15_postgres
password = open("/run/pg_password", encoding="utf-8").read()
dsn = make_conninfo(host="postgres-logs", dbname="ramon_logs",
                    user="ramon_logger", password=password, connect_timeout=3)
pg, spreads = load_m15_postgres(dsn, "XAUUSD_l")
with sqlite3.connect("file:/run/ramon_data/ramon_history.sqlite3?mode=ro", uri=True) as db:
    sq = db.execute("""SELECT time, open, high, low, close, spread_points
       FROM history_bars WHERE symbol=? AND timeframe=? ORDER BY time""",
       ("XAUUSD_l", "M15")).fetchall()
pg_by_time = {b.time: (b.open,b.high,b.low,b.close,int(sp))
              for b,sp in zip(pg,spreads)}
sq_by_time = {int(t): (float(o),float(h),float(l),float(c),int(sp))
              for t,o,h,l,c,sp in sq}
missing = set(sq_by_time) - set(pg_by_time)
extra = set(pg_by_time) - set(sq_by_time)
mismatch = [t for t in sq_by_time.keys() & pg_by_time.keys()
            if any(abs(a-b)>1e-7 for a,b in zip(sq_by_time[t][:4],pg_by_time[t][:4]))
            or sq_by_time[t][4]!=pg_by_time[t][4]]
result = {"sqlite_rows":len(sq_by_time),"postgres_rows":len(pg_by_time),
          "sqlite_latest":max(sq_by_time,default=None),
          "postgres_latest":max(pg_by_time,default=None),
          "missing_in_postgres":len(missing),"extra_in_postgres":len(extra),
          "ohlc_or_spread_mismatches":len(mismatch),
          "example_missing_times":sorted(missing)[-5:],
          "example_mismatch_times":sorted(mismatch)[-5:],
          "parity":not (missing or extra or mismatch)}
print(json.dumps(result,indent=2))
'
