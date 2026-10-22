# Three-container TimescaleDB cutover

## Target topology

- `backend`: Ramon model API plus background collectors/reporting/outbox workers.
- `timescaledb`: a fresh TimescaleDB/PostgreSQL instance with its own named volume.
- `dashboard`: the monitor, market dashboard, and training dashboard on ports 8013, 8014, and 8015.

This document is a cutover checklist, not an assertion that the current source is already safe to deploy in that topology.

## Current blockers found in source

1. Core market history and inference/trade telemetry have a mix of SQLite, PostgreSQL primary-write, and PostgreSQL shadow-write paths. `RAMON_STORAGE_BACKEND=postgres` does not switch every query in the application.
2. The monitor and market dashboard call code paths that contain direct SQLite queries as well as PostgreSQL-aware branches.
3. Target-outcome backfill and reversal report logic still depend on SQLite-oriented query implementations.
4. The training dashboard previously loaded examples through SQLite even when PostgreSQL training was selected. The training dashboard now routes example reads through `read_training_examples` when `RAMON_TRAINING_BACKEND=postgres`, and its test asserts that SQLite is not opened in that mode.
5. Training data is currently represented in `ramon_migration.training_decision_samples` and `ramon_migration.training_trade_outcomes`; this is not automatically populated by a clean operational TimescaleDB database. The cutover must explicitly define how operational writes feed the trainer's schema.
6. The operational DDL currently creates ordinary PostgreSQL tables. Timescale hypertables and retention/index policy must be created deliberately for time-series tables; they must not be assumed to exist merely because the image is TimescaleDB.

## Required implementation sequence

1. Keep the existing Compose configuration and all local MT5/source changes intact.
2. Finish the PostgreSQL storage contract for all writes and reads used by `server`, `monitor`, `market_dashboard`, `training_dashboard`, `target_outcomes`, `reversal_daily_report`, `mtf_collector`, and execution-health reporting.
3. Add isolated PostgreSQL/TimescaleDB integration tests for market bars, decision samples, trade outcomes, target outcomes, dashboard snapshot reads, manual overrides, MTF bars, and training selection. Tests must never connect to a live trading database.
4. Add a separate three-service Compose file and process supervisors. The backend group must shut down child processes cleanly; dashboard ports must each have a health check.
5. Validate Compose syntax, image build, health checks, empty-database startup, and end-to-end writes/reads before changing the default deployment.
6. Only after parity and restart tests pass, switch the active deployment. Do not run `docker compose down -v` or delete existing volumes as part of this cutover.
7. Keep rollback available until the new database has passed the agreed live-observation period.

## Definition of done

- Compose has exactly three services: `backend`, `timescaledb`, and `dashboard`.
- All operational and training reads/writes use the same fresh TimescaleDB instance; no silent SQLite fallback is possible in PostgreSQL mode.
- All three dashboard URLs work and report the same database/backend state.
- Integration tests prove an empty-database bootstrap and a full market → decision → trade → target-outcome lifecycle.
- Existing MT5 files, local uncommitted changes, and old volumes remain untouched until explicit cutover approval.
