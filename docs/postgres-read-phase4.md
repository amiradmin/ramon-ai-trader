# Phase 4: isolate the PostgreSQL M15 read path

This PR adds `ramon.postgres_history.load_bars_postgres(dsn, symbol)`, returning
the same `(bars, spreads)` shape as `history.load_bars` for M15.
It reads only `ramon_migration.history_bars` and is deliberately
**not connected to the live model, training, dashboards, or Compose**.

## Test

```bash
git fetch origin
git switch migration/postgres-history-read-parity
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync python -m pytest -q \
  tests/test_postgres_history.py tests/test_mtf_postgres_shadow.py
```

The tests use fake PostgreSQL connections and perform **no live DB reads/writes**.

## Validation gate before production cutover

- The repository now has a compatible *read-only* adapter, not a full
  PostgreSQL backend for Ramon.
- Verify on HP-Mini that the host or an isolated test container can
  reach the existing `ramon-ai-trader-postgres-logs-1` via a
  least-privilege PostgreSQL connection.
- Re-archive and refresh typed projections from a consistent snapshot,
  then compare all M15 rows and spreads with live SQLite, including
  recent inserts; current archived data may be stale.
- Update all remaining SQLite-dependent writers/readers in a separate
  protected rollout. Provide rollback and test decision parity.
- **Never delete `data/ramon_history.sqlite3` based on this phase.**

No Docker or EA restart is needed to test this PR.
