-- Read-only quality checks for Ramon research telemetry.
-- Run with: psql -U ramon_logger -d ramon_logs -f logging/telemetry_quality_0599.sql
-- These are diagnostics, NOT proof that execution outcome labels are causally correct.
SELECT kind, count(*) AS events, max(observed_at) AS latest
FROM raw_research_events
WHERE event_data->'data'->>'ea_version' = '0.59.9'
   OR (observed_at >= timestamptz '2026-10-13 06:00:00+00'
       AND event_data->>'role' IN ('MAIN','SMALL'))
GROUP BY kind ORDER BY kind;

SELECT count(*) AS bad_sample_keys
FROM raw_research_events
WHERE kind IN ('decision_gate','order_intent','order_result','position_quote','deal')
  AND observed_at >= timestamptz '2026-10-13 06:00:00+00'
  AND length(coalesce(sample_key,'')) NOT IN (0,16);

SELECT count(*) AS unmatched_order_results
FROM raw_research_events r
WHERE r.kind='order_result'
  AND r.observed_at >= timestamptz '2026-10-13 06:00:00+00'
  AND NOT EXISTS (
    SELECT 1 FROM raw_research_events i
    WHERE i.kind='order_intent'
      AND i.account_key=r.account_key
      AND i.sample_key=r.sample_key
      AND i.observed_at BETWEEN r.observed_at - interval '2 minutes' AND r.observed_at + interval '10 seconds'
  );

SELECT count(*) AS duplicate_event_uid_groups
FROM (SELECT event_uid FROM raw_research_events GROUP BY event_uid HAVING count(*)>1) d;

SELECT count(*) AS null_or_future_observed_time
FROM raw_research_events
WHERE observed_at IS NULL OR observed_at > now()+interval '5 minutes';

SELECT source_name, source_offset, error_code, first_seen_at
FROM ingest_errors ORDER BY first_seen_at DESC LIMIT 10;
