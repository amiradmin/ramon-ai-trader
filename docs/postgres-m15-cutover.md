# First production-path PostgreSQL cutover (M15 only)

This branch implements **opt-in** switches; no Docker Compose environment
is changed and the live EA continues with SQLite until a controlled rollout.

- `RAMON_M15_HISTORY_BACKEND=postgres`: `ramon.history.load_bars`
  reads typed M15 bars from `ramon_migration.history_bars`.
- `RAMON_M15_HISTORY_FALLBACK_SQLITE=1`: opt-in fallback when PostgreSQL
  reads fail. Recommended during canary rollout, otherwise errors propagate.
- `RAMON_M15_WRITE_BACKEND=postgres`: M15 history is written to
  PostgreSQL first, then SQLite is also written for legacy readers.
  PostgreSQL write failure is logged without revealing credentials;
  SQLite writing remains available.
- `RAMON_PG_HISTORY_DSN`: PostgreSQL connection (protected secret,
  never commit or paste the password).

**Limitations:** this is not a full PostgreSQL migration. Other operations
inside `history.py` (decision samples, trade labels, M1/M5, shadow votes,
target outcomes), training and dashboards still read/write SQLite. A
PostgreSQL-write failure can leave newer data only in SQLite; turn on the
read fallback and reconcile before considering any primary cutover.
The PG-first write is **synchronous** and can increase /decision latency;
do not enable it on a live account without measured latency/load, parity,
a reachable DSN, rollback procedure and successful dry-run tests.
No service restarts are necessary to review these changes.

## Host tests

```bash
git fetch origin
git switch migration/postgres-primary-m15-history
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync python -m pytest -q \
  tests/test_postgres_m15_history_backend.py \
  tests/test_postgres_m15_primary_write.py \
  tests/test_postgres_shadow_queue.py \
  tests/test_storage_timing.py \
  tests/test_postgres_market_shadow.py
```

Keep SQLite data, backup files and Docker volumes intact.
