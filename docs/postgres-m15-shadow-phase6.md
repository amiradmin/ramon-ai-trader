# M15 optional dual-write shadow (phase 6)

The live Model API continues to write completed M15 candles to SQLite.
After a successful SQLite write only, it can optionally mirror the
same bars to `ramon_migration.history_bars` using PostgreSQL upserts.
The exact historical spread policy is preserved: a zero-spread
older bar does not overwrite a previously known nonzero spread.

## Safety

- **Off by default**: no Compose changes and no trading cutover.
- The optional DSN is read from `RAMON_PG_MARKET_SHADOW_DSN`. Do not
  commit a password or post connection strings in chat.
- PostgreSQL failures are logged by exception type only and cannot
  change the `persist_market_safely` result, decision or execution.
- SQLite failure skips PostgreSQL, preserving SQLite as authoritative.
- Even a successful shadow write does **not** mean parity or that SQLite
  can be deleted.
- Requires optional dependency `psycopg[binary]>=3.1,<4`, a reachable
  database and INSERT/UPDATE rights to `ramon_migration.history_bars`.
  The Postgres logs container is in a separate Compose/network setup;
  do not enable without verifying reachability and least privilege.

## Tests on HP-Mini

```bash
git fetch origin
git switch migration/postgres-m15-shadow-dualwrite
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync python -m pytest -q \
  tests/test_postgres_market_shadow.py \
  tests/test_postgres_history.py \
  tests/test_mtf_postgres_shadow.py
```

Tests use mocks, never write to real PostgreSQL. Do not restart Docker.

## Before controlled rollout

1. Back up SQLite and PostgreSQL and confirm disk space.
2. Verify database schema and network, credentials via a protected
   environment variable and least-privilege role.
3. Test one write using a separate shadow-only test process, and verify
   payload parity and idempotence.
4. Wire DSN into Model API **only after** those tests and recheck logs.
5. Monitor freshness and OHLC/spread parity for multiple market days.
6. Keep a fast rollback: remove the DSN and recreate the service.

Remaining: all non-M15 writers, decision/trade consistency,
training/dashboard reads, observability and backup/restore tests.
