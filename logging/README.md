# Ramon: isolated PostgreSQL research logging (experimental)

This stack is **opt-in** and is included in the main `compose.yaml` under the `logging` profile. It never places orders,
does not call the EA decision endpoint and does not alter existing SQLite databases.
The production EA, MT5 and model keep using their existing paths.

## Setup on HP-Mini

1. Create a strong local password (do not commit it):
   `printf 'RAMON_LOG_DB_PASSWORD=%s\n' 'YOUR_STRONG_URL_SAFE_PASSWORD' >> .env`
   Use URL-safe password characters (letters, digits, hyphens) because the password
   is embedded in a database connection URI.
2. If using the separate `ramon-logging` worktree, set `RAMON_LOG_SOURCE_DATA_DIR=../ramon-ai-trader/data` in that worktree's `.env` to read the live source SQLite rather than an empty worktree data directory. Validate: `docker compose --profile logging config --quiet`
3. Start **only** the logging services in this worktree, using a unique project name to avoid starting/altering live stack:
   `docker compose -p ramon-logging --profile logging up -d --build postgres-logs research-log-ingest`
4. Watch: `docker compose -p ramon-logging --profile logging logs -f research-log-ingest`
5. Inspect:
   `docker compose -p ramon-logging --profile logging exec -T postgres-logs psql -U ramon_logger -d ramon_logs -c 'SELECT * FROM logging_freshness;'`

The existing research JSONL from 1–6 October will backfill incrementally from the
start. Backfilling approximately 50K research lines and ~94K decisions may take
multiple polling cycles. This importer **cannot regenerate events that the EA
never wrote**; the missing EA Research writer must be restored and compiled separately.

## Tables for analytics and supervised learning

- `raw_research_events`: append-only original JSON with source offsets, event IDs,
  account, sample and position identifiers.
- `position_quotes`: time-series bid/ask, floating PnL, SL/TP and lot volume.
- `position_actions`: deal, close, order, protection and intervention actions,
  with original event and actor classification (UNKNOWN unless explicitly supplied).
- `decision_features`: decision-time-only features and model snapshots.
- `trade_labels`: realized outcome labels and source, entry/exit metadata.
- `training_auto_closed_trades`: an initial **candidate** training view of completed
  automatic trades without explicitly marked human intervention.
  Review provenance and leakage before treating rows as validated ground truth.
- `logging_freshness`, `ingest_cursors`, `ingest_errors`: operations/audit.

Do not train with `trade_labels` fields as predictors; they are targets. Do not
naively random-split time-series samples. Use chronological splits, purge/embargo,
out-of-sample trading costs and a minimum sufficient sample size. A `NULL`
`manual_intervention` means **not recorded**, not proof of no intervention.

## Operational safety

- No public PostgreSQL port, named database volume, read-only mounted MT5 and SQLite inputs.
- Committed byte offsets follow successful inserts, so restarts resume safely.
- Duplicate event IDs and trade keys are ignored/upserted as appropriate.
- Trailing partial JSONL lines are not consumed; malformed complete lines are
  logged in `ingest_errors` and skipped.
- This ingest is separate from the live trading control plane. A PostgreSQL
  outage should not block trading, though analytics will lag.
- Avoid logging account identifiers/personal information outside protected systems.
  Limit database access and arrange backups/retention before long-term production use.

### Verification and rollback

`docker compose -p ramon-logging --profile logging ps`

`docker compose -p ramon-logging --profile logging exec -T postgres-logs psql -U ramon_logger -d ramon_logs -c 'SELECT entry_source,count(*),sum(net_units) FROM trade_labels GROUP BY entry_source;'`

`docker compose -p ramon-logging --profile logging down` stops only this optional stack.
**Do not use `down -v`**, as that deletes the PostgreSQL data volume.

## Not yet implemented in this branch

The installed live EA currently lacks the old ResearchAppend writer.
It must be restored with separate EA compilation and validation. The
historical Research SQLite import route is not yet rewritten to use PostgreSQL,
and dashboards still read their existing stores. This logging stack is a
safe first stage, not a complete automatic repair of all telemetry gaps.
