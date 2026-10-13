# PostgreSQL-only storage migration — live-account safety gate

Goal: migrate **all** Ramon persistent history, logging and research data from
SQLite to PostgreSQL with structured, queryable, append-only event records.
This is an **implementation plan, not a claim that SQLite has been removed**.

## Current dependencies found in main
- `src/ramon/history.py`: SQLite schema and write/read operations for history
  bars, decision samples, trade outcomes and learning data.
- `src/ramon/monitor.py`: read-only SQLite dashboard data and controls.
- `src/ramon/evaluation_lab.py`,
  `src/ramon/early_exit_replay_validator.py`,
  `src/ramon/missed_entry_audit.py`: SQLite research readers.
- `compose.yaml`: model, dashboard and MTF collector reference
  `/data/ramon_history.sqlite3` or `./data` bind mounts.
- Other consumers must be enumerated using repository-wide search before cutover.

## Migration sequence
1. Inventory every file and container using SQLite, including training and
   monitoring processes; define a table-by-table Postgres schema with
   unique keys, UTC timestamps, source provenance, payload schema version,
   immutable event_uid, and manual-intervention flags.
2. Provision a **persistent** PostgreSQL Docker volume, readiness health check,
   separate app/read-only accounts, backups, migrations and retention policy.
   Do not expose the database port beyond the Docker network.
3. Export a consistent SQLite snapshot; backfill Postgres with idempotent imports
   (including `history_bars`, `decision_samples`, `trade_outcomes` and event
   provenance); compare counts, UTC bounds, important aggregates and hashes,
   and confirm data source quality.
4. Adapt **all** Python writers/readers and dashboards to a single Postgres
   repository/data-access interface. Handle transactions, retries, pooling,
   health checks and unavailable-DB behavior explicitly.
5. Run dual-write/shadow-read with quantitative parity alerts before promotion.
   Ensure no duplicate learning samples, no missing or mixed manual provenance,
   and no live trading dependence on research database availability.
6. Switch reads and writes under controlled maintenance/restart; validate
   restarts and failure recovery, then stop old SQLite writers.
7. Only after independent backup restoration tests and signed-off parity,
   archive/remove SQLite files, dependencies, scripts and compose settings.
   Never delete the sole copy of historical data.

An existing historical branch
`feature/postgres-research-telemetry-20261013` contains early
`logging/schema.sql` Postgres event-store groundwork. Review/rebase it;
it covers event telemetry only, NOT the full project migration.

## Structured logging policy
- Types: `decision_snapshot`, `decision_gate`, `order_intent`,
  `order_result`, `broker_fill`, `position_management`,
  `manual_intervention`, `close_event`, `data_quality`.
- Each log event: UTC event time, broker event time, account identity hash,
  symbol, position ID / decision sample ID, model and EA versions,
  source `AUTO_RAMON` vs `HUMAN_ASSISTED`, gate code and evidence,
  severity, trace ID, normalized payload.
- Exclude credentials, tokens and personal secrets. Batch/partition historical
  raw ticks separately; do not flood a transactional event table with ticks.
- Prevent silent loss: bounded queues, retry/backoff, ingest dead-letter records,
  observable lag metrics, idempotent event IDs.
- Keep raw evidence immutable; train on separate validated projections only.

## New missed-entry action
`/missed-entry-audit` uses historical decision samples to show gate reasons
since local browser midnight. It is observational and not a proof of a missed
profitable trade. Full causality also requires correlation with order-intent,
order-result, broker journal, trading permissions and price/volume/spread data.
