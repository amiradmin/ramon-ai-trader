-- Raw, lossless event store and typed training-ready projections.
-- Timestamps are UTC; original source timestamps/JSON are retained.
CREATE TABLE IF NOT EXISTS raw_research_events (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 event_uid text NOT NULL UNIQUE,
 source_file text NOT NULL,
 source_offset bigint NOT NULL,
 kind text NOT NULL,
 account_key text,
 sample_key text,
 position_id text,
 observed_at timestamptz,
 broker_time_msc bigint,
 event_data jsonb NOT NULL,
 ingested_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS raw_research_sample_idx ON raw_research_events(sample_key);
CREATE INDEX IF NOT EXISTS raw_research_pos_time_idx ON raw_research_events(account_key,position_id,observed_at);
CREATE INDEX IF NOT EXISTS raw_research_kind_time_idx ON raw_research_events(kind,observed_at DESC);
CREATE TABLE IF NOT EXISTS ingest_cursors (
 source_name text PRIMARY KEY,
 source_identity text NOT NULL,
 next_byte_offset bigint NOT NULL DEFAULT 0,
 last_success_at timestamptz NOT NULL DEFAULT now(),
 CHECK (next_byte_offset >= 0)
);
CREATE TABLE IF NOT EXISTS ingest_errors (
 source_name text NOT NULL,
 source_offset bigint NOT NULL,
 error_code text NOT NULL,
 first_seen_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(source_name,source_offset,error_code)
);
-- Immutable decision features, as observed AT decision time (no future labels).
CREATE TABLE IF NOT EXISTS decision_features (
 sample_key text PRIMARY KEY,
 source_decision_id bigint NOT NULL,
 symbol text NOT NULL,
 quote_at timestamptz,
 captured_at timestamptz,
 signal_bar_at timestamptz,
 direction text,
 base_decision text,
 final_decision text,
 mid double precision,
 spread double precision,
 atr double precision,
 model_bundle text,
 regime_features jsonb NOT NULL DEFAULT '{}'::jsonb,
 entry_features jsonb NOT NULL DEFAULT '{}'::jsonb,
 news_features jsonb NOT NULL DEFAULT '{}'::jsonb,
 decision_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
 target_structure jsonb NOT NULL DEFAULT '{}'::jsonb,
 refreshed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS decision_features_time_idx ON decision_features(quote_at DESC);
-- Trade results, separate from features to avoid training target leakage.
CREATE TABLE IF NOT EXISTS trade_labels (
 trade_key text PRIMARY KEY,
 sample_key text,
 symbol text NOT NULL,
 side text NOT NULL,
 opened_at timestamptz,
 closed_at timestamptz,
 net_units double precision,
 initial_risk_units double precision,
 net_r double precision,
 profit_units double precision,
 commission_units double precision,
 swap_units double precision,
 fee_units double precision,
 exit_reason text,
 exit_detail text,
 entry_source text,
 trade_role text,
 entry_magic bigint,
 manual_intervention text,
 actual_fill_price double precision,
 training_status text,
 entry_ea_version text,
 original jsonb NOT NULL,
 synced_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS trade_labels_sample_idx ON trade_labels(sample_key);
CREATE INDEX IF NOT EXISTS trade_labels_close_idx ON trade_labels(closed_at DESC);
CREATE INDEX IF NOT EXISTS trade_labels_source_idx ON trade_labels(entry_source,closed_at DESC);
-- Time-series observations supplied by research events.
CREATE TABLE IF NOT EXISTS position_quotes (
 raw_event_id bigint PRIMARY KEY REFERENCES raw_research_events(id) ON DELETE CASCADE,
 account_key text,
 sample_key text,
 position_id text,
 observed_at timestamptz,
 bid double precision,
 ask double precision,
 floating_profit_units double precision,
 volume double precision,
 sl double precision,
 tp double precision,
 tp_stage integer,
 event_data jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS position_quotes_lookup_idx ON position_quotes(account_key,position_id,observed_at);
-- Auditable human/EA actions; raw payload retained.
CREATE TABLE IF NOT EXISTS position_actions (
 raw_event_id bigint PRIMARY KEY REFERENCES raw_research_events(id) ON DELETE CASCADE,
 account_key text,
 sample_key text,
 position_id text,
 observed_at timestamptz,
 action_kind text NOT NULL,
 actor_class text NOT NULL DEFAULT 'UNKNOWN',
 reason text,
 event_data jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS position_actions_lookup_idx ON position_actions(account_key,position_id,observed_at);
-- Training set: only features available at decision time and completed, unassisted auto labels.
-- Never use target columns as input features. Validate chronological split/purge separately.
CREATE OR REPLACE VIEW training_auto_closed_trades AS
 SELECT t.trade_key,t.sample_key,t.symbol,t.side,t.opened_at,t.closed_at,
        t.entry_source,t.trade_role,t.entry_ea_version,
        d.quote_at,d.signal_bar_at,d.base_decision,d.final_decision,
        d.mid,d.spread,d.atr,d.model_bundle,
        d.regime_features,d.entry_features,d.news_features,d.decision_metadata,
        t.net_units AS label_net_units,t.net_r AS label_net_r,
        t.exit_reason AS label_exit_reason,t.exit_detail AS label_exit_detail
 FROM trade_labels t
 JOIN decision_features d ON d.sample_key=t.sample_key
 WHERE t.entry_source IN ('AUTO_RAMON','RANGE_AUTO')
   AND t.manual_intervention IS NULL
   AND t.closed_at IS NOT NULL AND t.net_r IS NOT NULL
   AND t.training_status IN ('LEARNABLE','READY');
-- Monitoring for delays or stopped publishers.
CREATE OR REPLACE VIEW logging_freshness AS
 SELECT 'decision_features' AS source, count(*) AS rows, max(quote_at) AS latest_at FROM decision_features
 UNION ALL SELECT 'trade_labels',count(*),max(closed_at) FROM trade_labels
 UNION ALL SELECT 'research_events',count(*),max(observed_at) FROM raw_research_events
 UNION ALL SELECT 'position_quotes',count(*),max(observed_at) FROM position_quotes;
