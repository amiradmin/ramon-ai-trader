\set ON_ERROR_STOP on
-- Run as the existing PostgreSQL administrator. Password is provided with
-- psql -v lab_password=...; do not commit secrets.
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', 'ramon_lab_reader', :'lab_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='ramon_lab_reader') \gexec
SELECT format('ALTER ROLE %I LOGIN PASSWORD %L', 'ramon_lab_reader', :'lab_password') \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), 'ramon_lab_reader') \gexec
GRANT USAGE ON SCHEMA public TO ramon_lab_reader;
GRANT SELECT ON TABLE public.ea_bridge_marketbar TO ramon_lab_reader;
-- The existing TREND_PULLBACK research pipeline reads the standard
-- opportunity/entry/risk thresholds from this table while constructing
-- its detector. Access is SELECT only; live configuration cannot be changed.
GRANT SELECT ON TABLE public.ea_bridge_decisionthresholdconfig TO ramon_lab_reader;
ALTER ROLE ramon_lab_reader SET default_transaction_read_only = on;
ALTER ROLE ramon_lab_reader SET statement_timeout = '120s';
-- Explicitly do not grant inserts, updates, deletes, sequences, or other tables.
