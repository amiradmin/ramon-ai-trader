# Ramon Django pipeline modules

The pipeline is staged into separate Python modules and is being introduced alongside
(not in place of) the current EA bridge.

## Implemented foundation
- `ingestion.py`: request-size validation and event classification helper.
- `django_adapter.py`: Redis connection and pipeline construction.
- `redis_buffer.py`: Redis Streams lanes for market, execution, and telemetry.
- `management/commands/consume_ramon_streams.py`: consumer groups, pending-entry reclaim,
  transactional persistence, idempotent upserts, bounded retries and dead-letter stream.
- `market_data.py`: UTC and OHLCV normalization.
- `strategy.py`, `risk.py`, `execution.py`: contracts for decisions, independent risk and intents.
- `persistence.py`: repository ports.
- `learning_telemetry.py`: outcome attribution contract.
- `recovery.py`: retry policy and UTC parsing.
- `schemas.py`: shared versioned event envelope.

## Data model
The `ea_bridge` app now has models/migration for normalized market bars, strategy decisions,
risk verdicts and order intents. Existing EAHeartbeat/EAEvent models remain in place.

## Safe rollout
The existing synchronous event-ingestion path remains the default. Set
`RAMON_PIPELINE_INGEST_ENABLED=1` only after Redis and the stream worker are healthy and
integration tests have passed. If Redis is unavailable in pipeline mode, the endpoint
returns 503 so the EA can retry; it does not silently fall back to a potentially overloaded DB.

Compose starts a private Redis service and worker. Redis has AOF enabled and a no-eviction
policy; market/telemetry streams have a trim target while execution events are not auto-trimmed.
Monitor disk/memory/stream growth and configure operational alerts before enabling ingestion.

## Not yet a live trading engine
The strategy/risk contracts are not connected to Ramon’s existing model-serving logic; order
intents are not exposed to the EA, and no Django code authorizes live trades. The EA command /
acknowledgement protocol, risk policy implementation, full outcome reconciliation and demo-account
integration tests remain follow-up tasks. Do not delete `backend/ramon` until behavior is migrated
and regression-tested.
