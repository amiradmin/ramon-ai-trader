# Phase 3 — typed PostgreSQL market storage preparation

This change **does not switch live Ramon** to PostgreSQL. SQLite remains
the operating database for model decisions, dashboards, model training,
history replay and MTF collector. Do not delete SQLite.

## What this phase does

`logging/typed_market_migration.sql` creates native typed versions of
`history_bars` and `mtf_utc_bars` under an isolated
`ramon_migration` PostgreSQL schema. It reads the rows already stored in
`sqlite_archive.rows` and upserts by (symbol,timeframe,time).
It does not modify the existing `public` log tables or reroute trading.
The transaction rolls back if a conversion fails.

## Host procedure — only after backup and disk check

```bash
cd ~/Documents/Presentation/ramon-ai-trader
df -h .
docker exec ramon-ai-trader-postgres-logs-1 \
  psql -U ramon_logger -d ramon_logs -c \
  "SELECT source_table, count(*) FROM sqlite_archive.rows WHERE source_table IN ('history_bars','mtf_utc_bars') GROUP BY 1;"
docker exec -i ramon-ai-trader-postgres-logs-1 \
  psql -X -v ON_ERROR_STOP=1 -U ramon_logger -d ramon_logs \
  < logging/typed_market_migration.sql
```

Expected first-run counts from the earlier host output: 24,379 historical
bars and 3,405 MTF UTC bars. Counts can increase due to live activity.

Compare source data, not just counts, before claiming parity:

```sql
SELECT a.source_rowid
FROM sqlite_archive.rows a
LEFT JOIN ramon_migration.history_bars b
 ON b.symbol=a.payload->>'symbol'
 AND b.timeframe=a.payload->>'timeframe'
 AND b.time=(a.payload->>'time')::bigint
WHERE a.source_table='history_bars'
 AND (b.time IS NULL
   OR b.open IS DISTINCT FROM (a.payload->>'open')::double precision
   OR b.high IS DISTINCT FROM (a.payload->>'high')::double precision
   OR b.low IS DISTINCT FROM (a.payload->>'low')::double precision
   OR b.close IS DISTINCT FROM (a.payload->>'close')::double precision
   OR b.spread_points IS DISTINCT FROM (a.payload->>'spread_points')::integer)
LIMIT 20;
```

No rows means all historical OHLC rows currently archived have a matching
typed row, but doesn't guarantee fresh data or readiness for cutover.

## Still blocking full migration

1. Replace all sqlite3-dependent APIs with PostgreSQL equivalents and
   validate correct transactions, concurrent writes and read-only queries.
2. Replace shadow/training/reporting readers, including inference audits,
   manual overrides, target outcomes and all model inputs.
3. Ensure current live bars and training labels are written into PostgreSQL,
   rather than relying on manual archive refreshes.
4. Run integration tests and compare decision outcomes, dashboard content
   and training data on the actual HP-Mini, then perform a controlled restart.
5. Validate backup/restore and an explicit rollback to SQLite before cutover.

No EA execution behavior or risk controls should change as part of storage
migration. Keep the SQLite database and existing Compose settings intact.
