# M15 PostgreSQL Shadow reconciliation — phase 9

The new script can repair gaps caused by the bounded in-memory async
shadow queue (which can drop writes on overload or restart). It
reads a consistent snapshot of live SQLite M15 bars and idempotently
upserts them to `ramon_migration.history_bars`.

Default is **dry-run**. PostgreSQL changes require an explicit
`--execute`. This is a manual recovery tool, **not** automatic
real-time replication or a replacement for SQLite.

## Offline tests (no database changes)

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync python -m pytest -q \
  tests/test_postgres_m15_reconcile.py
```

## Inspect only

```bash
PYTHONPATH=src python scripts/reconcile_postgres_m15.py \
  --sqlite data/ramon_history.sqlite3
```

The live logging PostgreSQL container is not automatically accessible from
Ubuntu. For a future execution, provide a reachable PostgreSQL connection
through a protected secret configuration; do not expose passwords in Git,
logs, terminal history or chat.

The current script replays all available M15 bars each time and may
generate substantial database writes. It intentionally does not run on a
schedule or inside the Model API request path. Before using `--execute`
on the live PostgreSQL database: verify backups, available disk, user
permissions and throughput, then validate actual SQLite-to-Postgres
parity. Confirm the destination is specifically the migration schema
in `ramon_logs`, not an arbitrary database.

**Do not delete SQLite** based on a successful reconciliation run.
Additional model, training, dashboards, reports and operational
writers/readers remain SQLite-dependent.
