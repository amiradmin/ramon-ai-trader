# Stage 9 — failure recovery

Specify behavior for Django/Redis restart, DB outage, EA disconnect, duplicate or reordered
events, consumer crash and broker timeout. Use retries with backoff, bounded delivery
counts, dead-letter streams, lag metrics and a reconciliation job comparing persisted
intents/positions with MT5. HTTP health alone does not mean trading-ready.
