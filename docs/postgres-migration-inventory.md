# PostgreSQL migration inventory

Status: **not ready for SQLite retirement** (audited 2026-10-13).

## Completed and verified in this change

- Training examples are now selected natively from PostgreSQL JSONB staging
  tables. The reader opens a read-only PostgreSQL transaction and has no
  in-memory SQLite compatibility database.
- The query preserves the current eligibility predicate and ordering:
  symbol/model, schema version, news snapshot, direction, learnable status,
  dashboard-opportunity exception, timing bounds, and
  `quote_time/sample_key/trade_key` ordering.
- `scripts/deploy_pg_training_canary.sh` validates configuration, focused
  regression tests, PostgreSQL health, and the first successful sync. Any
  failure after startup stops **only** `pg-training-sync`.

## Existing PostgreSQL coverage

| Data | PostgreSQL state | Cutover state |
| --- | --- | --- |
| M15 history bars | Typed `ramon_migration.history_bars`, parity tooling | Opt-in reader only |
| Decision samples / trade outcomes | Lossless JSONB staging and native trainer read | Sync remains SQLite-source |
| Research decisions / labels / telemetry | `logging/schema.sql` typed tables | Research logger only |
| MTF bars | Archive/typed migration tooling | SQLite writer/reader remains active |

## Remaining SQLite runtime dependencies

`compose.yaml` still passes a SQLite path to `model`, `dashboard`,
`market-dashboard`, `training-dashboard`, `mtf-collector`,
`reversal-daily-report`, `kronos-advisor`, and several audit services.
The model server and `history.py`, `mtf_collector.py`, `monitor.py`,
`training_dashboard.py`, `report.py`, and `human_review.py` remain direct
SQLite consumers. The repository also contains many intentionally offline
research/replay/audit tools using SQLite fixtures or read-only snapshots.

This is therefore an implementation inventory, not evidence that a running
service is safe to switch. Regenerate the exact source list before each gate:

```bash
rg -l '^import sqlite3|^from sqlite3 import' src/ramon logging scripts | sort
```

## Staged deployment gates

1. Back up SQLite and PostgreSQL using the HP-Mini operations process; verify
   restore in an isolated environment. Do not use this repository to delete
   either backup.
2. Run the canary script. It does not restart a live trading container or
   change an EA:

   ```bash
   scripts/deploy_pg_training_canary.sh
   ```

3. Require a successful sync, exact primary-key coverage, payload digest and
   timestamp-range parity for every mapped table.
4. Exercise every PostgreSQL reader in a non-live environment, including
   dashboards, MTF collection, reporting, manual-intervention telemetry,
   training, and history replay.
5. Perform an explicitly approved, reversible reader-only canary. Keep
   SQLite writes and the rollback switch in place until fresh-data parity is
   observed for an agreed window.

## SQLite retirement procedure (blocked pending explicit approval)

Retirement may begin only after all entries above have passed, no deployed
service mounts or references a SQLite database, and an operator has approved
the exact source and archive targets. First stop SQLite writers in a planned
maintenance window, snapshot and checksum the files, preserve them as a
read-only archive, test PostgreSQL restore and rollback, then observe the
PostgreSQL-only environment. Deletion is out of scope for automation and
requires a separate explicit approval.
