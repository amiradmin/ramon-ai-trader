# PostgreSQL training data path

This batch migrates decision samples and closed-trade outcomes to
`ramon_migration.training_decision_samples` and
`ramon_migration.training_trade_outcomes` as lossless JSONB. The
`RAMON_TRAINING_BACKEND=postgres` option selects PostgreSQL as the
source for trade-example generation (not yet all training data).

SQLite is still used by live execution, history writes, dashboard,
MTF collection and other training/reporting readers. These changes
remain disabled by default and do NOT authorize SQLite removal.

Run targeted tests before merge:
```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync python -m pytest -q tests/test_postgres_training_path.py
```

For isolated PG sync, use an existing tested image with read-only
source and password file; this writes ONLY to the two new PG tables:
```bash
docker run --rm --network ramon-ai-trader_default \
  -v "$PWD/src:/app/src:ro" -v "$PWD/scripts:/app/scripts:ro" \
  -v "$PWD/data:/data:ro" \
  -v "$PWD/.secrets/postgres-password:/run/secrets/postgres-password:ro" \
  -e PYTHONPATH=/app/src --entrypoint python \
  ramon-ai-trader:pg-canary-working /app/scripts/sync_pg_training.py
```

The sync is an idempotent **snapshot**, not a continuous writer. Run during
low activity; compare data counts and training parity before selecting the
PostgreSQL backend in a non-trading training container. Never paste a DSN
with credentials, enable it in the live model, or remove SQLite.
