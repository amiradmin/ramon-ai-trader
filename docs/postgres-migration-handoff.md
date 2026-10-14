# PostgreSQL migration handoff — 2026-10-13

## Branch and safety status

- Development branch: `codex/postgres-full-migration`
- Do **not** merge or deploy this branch yet.
- SQLite retirement and live PostgreSQL cutover are blocked.
- No live trading container, EA, SQLite file, PostgreSQL production database, or
  Docker volume was changed by this development work.

## Commits on this branch

| Commit | Change |
| --- | --- |
| `679e727` | Native read-only PostgreSQL training reader |
| `380f3cc` | Guarded additive migration orchestrator |
| `3e39dfe` | PostgreSQL operational market-history and decision-sample storage |
| `85ed8d3` | PostgreSQL trade-outcome and shadow-vote persistence |
| `c275b6f` | PostgreSQL replay-input persistence |
| `4284633` | Isolated PostgreSQL integration-test Compose environment |

## Verified checks

- Focused PostgreSQL/history regression suite: `13 passed`.
- Isolated PostgreSQL 17 Docker test: `scripts/run_postgres_integration.sh`;
  PostgreSQL reached healthy state and its focused suite reported `7 passed`.
- The test Compose project is `ramon-postgres-integration-test`, uses only a
  loopback port and tmpfs database storage, and removes itself on exit.

## Current implementation

With `RAMON_STORAGE_BACKEND=postgres` and `RAMON_PG_DSN` set, the following
paths use schema `ramon` in PostgreSQL:

- market history persistence and M15 reads;
- decision samples;
- trade outcomes and telemetry payload;
- shadow votes;
- replay-input / inference-audit payload;
- role-training reader (its staging source remains separately synchronized).

SQLite remains the default backend and fallback. The development branch must
not be enabled in a live trading service.

## Remaining work (migration is INCOMPLETE)

1. Port `target_outcomes` / Target Learning to PostgreSQL and test the full
   decision → trade → target-label flow.
2. Port manual-intervention tables in `server.py`.
3. Make MTF collector PostgreSQL-primary, then cover M1/M5 read paths.
4. Port dashboards, reporting, audit and operational CLI tools behind a shared
   PostgreSQL read interface; remove each direct `sqlite3` use only after its
   replacement test passes.
5. Add PostgreSQL-only Compose wiring for non-live test/canary services.
6. Expand isolated integration tests for API, trading telemetry and all
   PostgreSQL reads/writes. Do not use production PostgreSQL for these tests.
7. Run parity checks for keys, payload digests, timestamp ranges and fresh-data
   behavior. `migrate_to_postgres.sh` must remain fail-closed on deletion.

## Continue from another workstation

```bash
git clone https://github.com/amiradmin/ramon-ai-trader.git
cd ramon-ai-trader
git switch codex/postgres-full-migration
uv sync --extra postgres --extra dev
scripts/run_postgres_integration.sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -q \
  tests/test_postgres_training_path.py tests/test_postgres_history.py \
  tests/test_postgres_m15_history_backend.py tests/test_postgres_m15_reconcile.py
```

Do not run `migrate_to_postgres.sh --apply`, do not alter production Compose,
and do not restart MT5/model services until every operational dependency is
portably PostgreSQL-native and an explicit operator approval is available.
