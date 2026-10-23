# Ramon Django pipeline modules

The Django pipeline contains an event-ingestion/processing foundation and a newer modular SignalFlow decision pipeline. They are distinct at this stage; SignalFlow has not yet been wired into an EA `/decision` endpoint.

## Current modules

- `ingestion.py`, `schemas.py`: request/event classification and versioned envelopes.
- `django_adapter.py`, `redis_buffer.py`: Redis connection and Redis Streams buffering.
- `management/commands/consume_ramon_streams.py`: stream consumer, pending-entry recovery, transaction-before-ACK, idempotent persistence, retries, and dead-letter handling.
- `market_data.py`: UTC and OHLCV normalization.
- `strategy.py`, `risk.py`, `execution.py`: legacy strategy, risk, and order-intent contracts used by existing orchestration/tests.
- `orchestration.py`, `persistence.py`: decision/risk/intent persistence abstractions.
- `learning_telemetry.py`: outcome-attribution contract.
- `recovery.py`: retry policy and UTC parsing.
- `signalflow/opportunities.py`: opportunity filtering.
- `signalflow/entry_timing.py`: entry quality and spread checks.
- `signalflow/risk_management.py`: bounded risk-percent and volume sizing.
- `signalflow/learning.py`: validates and classifies an outcome; it does not persist or adapt from it yet.
- `signalflow/pipeline.py`: runs SignalFlow stages and returns BUY/SELL/HOLD with `execution_enabled: false`.

## Runtime configuration

Compose runs private Redis and TimescaleDB services plus a worker. Event ingestion is disabled by default with `RAMON_PIPELINE_INGEST_ENABLED=0`. Enable it only after the services and integration tests are verified. In ingestion mode, Redis failure returns a retryable 503 rather than silently acknowledging an event.

## Not yet a live trading engine

- The EA-facing `/decision` endpoint is not implemented in Django.
- SignalFlow is not integrated into the Django request path.
- Learning outcomes are not yet durably persisted by SignalFlow and do not update policy.
- Order intents are not exposed as EA commands; Django does not authorize live trades.
- Strategy parity, outcome reconciliation, and demo-account end-to-end tests remain outstanding.

Keep the legacy modules until their importers, worker, and tests have been migrated. Do not treat a passing health check as proof that decision-making or live execution is working.
