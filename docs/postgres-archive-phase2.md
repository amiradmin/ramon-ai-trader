# Ramon PostgreSQL migration: legacy SQLite archive (phase 2)

**This does not replace the SQLite storage backend. Never delete SQLite based on this step.**

The active postgres-logs instance is `ramon-ai-trader-postgres-logs-1`,
database `ramon_logs`. The `research-postgres` profile in the root
`compose.yaml` is not the active log database.

The first audit matched all nonempty `decision_samples.sample_key` and
`trade_outcomes.trade_key` values with PostgreSQL. Fourteen legacy tables
still lack equivalent verified operational tables. The additive archiver
preserves each legacy SQLite row as JSONB under
`sqlite_archive.rows(source_table,source_rowid,payload)`; BLOB columns use
`{"__sqlite_blob_base64__":"..."}` to preserve bytes. It does not promote
these JSONB documents into native SQL data models.

## Safety preflight on HP-Mini

1. Verify PostgreSQL storage and available free disk capacity; the SQLite
   database is large and JSONB copies can require substantially more space.
2. Take a **verified** SQLite backup and a PostgreSQL `pg_dump` in a safe
   backup location, not by overwriting either live database.
3. Inspect the dry run, which makes no writes:

```bash
cd ~/Documents/Presentation/ramon-ai-trader
python3 scripts/postgres_archive_sqlite.py
```

4. To archive selected, small legacy tables first (writes **only** in
   `sqlite_archive`):

```bash
python3 scripts/postgres_archive_sqlite.py --execute \
  --include human_market_opinions manual_gate_overrides target_outcomes
```

5. After checking disk, archive remaining tables using `--execute`
   with `--include` (or omit `--include` for all 14). The copy is
   idempotent on source table and rowid; it does **not** delete rows from
   the destination, so stale/deleted source records require a separate audit.
   The live SQLite database can change during the copy; repeat only after
   a controlled snapshot window before declaring parity.

6. Validate exact counts:

```bash
docker exec ramon-ai-trader-postgres-logs-1 \
  psql -U ramon_logger -d ramon_logs -c \
  "SELECT source_table, count(*) FROM sqlite_archive.rows GROUP BY 1 ORDER BY 1;"
python3 scripts/postgres_parity_audit.py --json
```

## Pending before real cutover

- Build typed PostgreSQL replacements for history bars, samples, audit,
  feature/label tables and research views, not just raw JSONB archiving.
- Switch model, collectors, monitors, dashboards, replay and training clients
  behind an appropriate database access layer.
- Test historical and current record parity, byte-level/BLOB integrity,
  UTC handling, schema constraints and ingestion error handling.
- Test health and cold restart on HP-Mini with a rollback and a safe market
  window. No changes to EA execution/risk logic until tests pass.
- SQLite deletion remains **blocked**.
