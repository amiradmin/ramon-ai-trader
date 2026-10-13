# PostgreSQL migration status and deletion gate (2026-10-13)

## What is verified

The working log database is `ramon-ai-trader-postgres-logs-1`, database
`ramon_logs`, user `ramon_logger`. The other observed container,
`ramon-logging-postgres-logs-1`, held empty tables at the time of inspection.
Do not remove either volume without independently identifying mounts/owners.

The live host reported 97,446 decision features, 468 trade labels,
51,792 raw research events, 45,098 position quotes and 393 position actions.
These are snapshots, not a parity certification. One UnicodeDecodeError
occurred at byte 32,651,756; ingest cursor later reached byte 42,088,690.
Advancement proves progress beyond the error, not lossless ingestion.

## Safe audit command

From the repository directory on HP-Mini:

```bash
python3 scripts/postgres_parity_audit.py --json > /tmp/ramon-pg-audit.json
cat /tmp/ramon-pg-audit.json
```

Exit status 2 is **intentional**: deletion is always blocked by this audit.
Exit 3 means audit failed. It does not modify either database.

The audit queries exact counts for all user tables and compares nonempty
`decision_samples.sample_key` against `decision_features.sample_key`,
and `trade_outcomes.trade_key` against `trade_labels.trade_key`.
It lists all SQLite tables for which equivalence has not been verified.
It does not compare payloads, time coverage, event offsets, or the behavior
of live services.

## Migration scope

The default `compose.yaml` still wires SQLite into `model`,
`dashboard`, `market-dashboard`, `training-dashboard`,
`mtf-collector`, `reversal-daily-report`, and `v2-execution-health`.
Core Python modules call sqlite3 directly. The separate
`research-postgres` compose profile is not the running
`postgres-logs` service observed on HP-Mini.

For safe cutover:
1. Identify the actual compose file/environment and mounts of postgres-logs.
2. Snapshot/backup PostgreSQL and SQLite with adequate free disk space.
3. Migrate every required SQLite table including bars, inference audit,
   signal outcomes, target outcomes, manual overrides, training metadata.
4. Match stable primary keys, exact row counts, payload checksums and
   first/latest timestamps; validate research JSONL offsets and errors.
5. Move readers/writers behind a PostgreSQL-compatible persistence API;
   verify dashboards, collectors, model snapshots, training and replay
   with tests and a cold restart, with no change to trading rules.
6. Observe fresh data in PostgreSQL with SQLite writers disabled only
   in a controlled test window. Preserve a rollback path.
7. Archive old SQLite files first; consider deletion only after explicit
   approval and successful cutover verification.

**Do not run `rm`, `docker compose down -v`, or change a running EA based
on the log count comparison alone.**
