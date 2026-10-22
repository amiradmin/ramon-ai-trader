# Ramon Django pipeline modules

Each stage has a separate Python module. These contracts and validation helpers
are an architectural first step; they are **not yet wired into the existing
Django EA views** and are not a complete live trading engine.

- `ingestion.py`: request size validation and event classification.
- `redis_buffer.py`: separate Redis Streams lanes for market, execution, telemetry.
- `market_data.py`: UTC and OHLCV normalization.
- `strategy.py`: injected strategy interface and decision validation.
- `risk.py`: independent fail-closed risk gate contract.
- `execution.py`: order intent and EA outcome contracts.
- `persistence.py`: idempotent repository ports for Django ORM adapters.
- `learning_telemetry.py`: decision-to-outcome attribution contract.
- `recovery.py`: retry/quarantine policy and UTC parsing.
- `pipeline.py`: top-level composition boundary.
- `schemas.py`: shared event envelope.

Before live use, implement ORM models/migrations, Redis worker loops and pending
recovery, EA command/ack protocol, actual strategy/risk adapters, integration
and failure tests. Redis is a buffer, not the durable source of truth. Do not
remove `backend/ramon` until legacy behavior has been inventoried, migrated and
regression-tested. Existing live decision endpoints remain unchanged for now.
