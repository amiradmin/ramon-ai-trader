# Stage 7 — durable persistence

PostgreSQL/TimescaleDB is authoritative for events, decisions, risk verdicts, order
intents, acknowledgements and outcomes. Use Django transactions, unique constraints,
bulk insert/upsert for market data and explicit retention/partition policies. Store
payload schema version and correlation ID. Redis loss must cause replay/reconciliation,
not loss of execution history.
