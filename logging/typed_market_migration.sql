-- Optional typed PostgreSQL market projections from archived SQLite JSONB.
-- Does not alter public.* or any live Ramon consumer.
-- Rerun after archives are refreshed; row-level upsert is idempotent.
BEGIN;
CREATE SCHEMA IF NOT EXISTS ramon_migration;
CREATE TABLE IF NOT EXISTS ramon_migration.history_bars (
  symbol text NOT NULL,
  timeframe text NOT NULL,
  time bigint NOT NULL,
  open double precision NOT NULL,
  high double precision NOT NULL,
  low double precision NOT NULL,
  close double precision NOT NULL,
  spread_points integer NOT NULL DEFAULT 0,
  PRIMARY KEY (symbol, timeframe, time)
);
CREATE TABLE IF NOT EXISTS ramon_migration.mtf_utc_bars (
  symbol text NOT NULL,
  timeframe text NOT NULL,
  time bigint NOT NULL,
  broker_time bigint NOT NULL,
  offset_seconds integer NOT NULL,
  open double precision NOT NULL,
  high double precision NOT NULL,
  low double precision NOT NULL,
  close double precision NOT NULL,
  spread_points integer NOT NULL,
  PRIMARY KEY (symbol, timeframe, time)
);
INSERT INTO ramon_migration.history_bars
 (symbol,time, timeframe,open,high,low,close,spread_points)
SELECT
 payload->>'symbol',(payload->>'time')::bigint,payload->>'timeframe',
 (payload->>'open')::double precision,
 (payload->>'high')::double precision,
 (payload->>'low')::double precision,
 (payload->>'close')::double precision,
 (payload->>'spread_points')::integer
FROM sqlite_archive.rows WHERE source_table='history_bars'
ON CONFLICT (symbol,timeframe,time) DO UPDATE
SET open=EXCLUDED.open, high=EXCLUDED.high,low=EXCLUDED.low,
 close=EXCLUDED.close,spread_points=EXCLUDED.spread_points;
INSERT INTO ramon_migration.mtf_utc_bars
 (symbol,timeframe,time,broker_time,offset_seconds,open,high,low,close,spread_points)
SELECT
 payload->>'symbol',payload->>'timeframe',
 (payload->>'time')::bigint,(payload->>'broker_time')::bigint,
 (payload->>'offset_seconds')::integer,
 (payload->>'open')::double precision,
 (payload->>'high')::double precision,
 (payload->>'low')::double precision,
 (payload->>'close')::double precision,
 (payload->>'spread_points')::integer
FROM sqlite_archive.rows WHERE source_table='mtf_utc_bars'
ON CONFLICT (symbol,timeframe,time) DO UPDATE
SET broker_time=EXCLUDED.broker_time,offset_seconds=EXCLUDED.offset_seconds,
 open=EXCLUDED.open,high=EXCLUDED.high,low=EXCLUDED.low,
 close=EXCLUDED.close,spread_points=EXCLUDED.spread_points;
COMMIT;

-- Explicit compare with archived source, both in the same PostgreSQL snapshot.
WITH checks AS (
 SELECT 'history_bars' AS name,
   (SELECT count(*) FROM sqlite_archive.rows WHERE source_table='history_bars') AS archive_count,
   (SELECT count(*) FROM ramon_migration.history_bars) AS typed_count
 UNION ALL
 SELECT 'mtf_utc_bars',
   (SELECT count(*) FROM sqlite_archive.rows WHERE source_table='mtf_utc_bars'),
   (SELECT count(*) FROM ramon_migration.mtf_utc_bars)
)
SELECT name,archive_count,typed_count,
 CASE WHEN archive_count=typed_count THEN 'COUNT_MATCH' ELSE 'COUNT_MISMATCH' END AS status
FROM checks ORDER BY name;
