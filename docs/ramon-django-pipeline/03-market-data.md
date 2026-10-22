# Stage 3 — market-data normalization and persistence

Normalize symbol, timeframe, OHLCV, event time and broker metadata. Reject non-finite
values, inconsistent highs/lows, negative volume and timezone-naive timestamps. Convert
times to UTC. Batch writes to PostgreSQL/TimescaleDB with a unique key such as
(symbol, timeframe, timestamp, source) to deduplicate. Preserve source metadata; label
rather than silently invent missing bars.
