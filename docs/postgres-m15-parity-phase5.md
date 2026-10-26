# M15 live parity verification

This script is **read-only**. It compares every M15 row from live SQLite
with typed PostgreSQL columns using the same `Bar` reader API.
It reports missing timestamps, extra timestamps, numeric differences,
and maximum bar times. A parity result **never permits SQLite deletion**.

## Test locally without touching live services

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync python -m pytest -q \
 tests/test_postgres_history.py tests/test_mtf_postgres_shadow.py
```

## Test against PostgreSQL

`scripts/audit_postgres_m15_parity.py` requires Python package
`psycopg[binary]`, a read-only PostgreSQL connection and an accessible
PostgreSQL port. The logs PostgreSQL container is not necessarily
exposed on the host. Prefer running this audit on an isolated container
attached to the same network rather than opening PostgreSQL to the internet.

Example after configuring a private read-only DSN in the host environment:

```bash
PYTHONPATH=src uv run --no-sync python scripts/audit_postgres_m15_parity.py \
 --sqlite data/ramon_history.sqlite3 --dsn "$RAMON_PG_READONLY_DSN"
```

For a nonempty live history, exit 0 = exact comparison match; exit 2 =
mismatch or stale data; exit 3 = unavailable or invalid database connection.
Do not print DSNs or share passwords.

The typed table is currently refreshed from an archived SQLite copy,
so recent SQLite changes can legitimately produce discrepancies.
Keep the live SQLite file, EA, Model API and dashboards untouched until
end-to-end PostgreSQL read/write cutover and rollback have been tested.
