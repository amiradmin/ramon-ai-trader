# Ramon Django-native pipeline — overview

## Goal
Move Ramon orchestration into Django in controlled stages while preserving MT5
execution safety. Do not delete legacy `backend/ramon` until parity is demonstrated.

```mermaid
flowchart TD
 EA[MT5 EA] --> ING[Django authenticated ingestion]
 ING --> MQ{Event class}
 MQ --> M[Redis market stream]
 MQ --> X[Redis execution stream]
 MQ --> T[Redis telemetry stream]
 M --> N[Normalize and batch]
 N --> DB[(PostgreSQL / TimescaleDB)]
 DB --> S[Strategy decision]
 S --> R[Independent risk gate]
 R -->|approved| O[Durable order intent / outbox]
 R -->|rejected| A[Audited rejection]
 O --> P[EA command polling]
 P --> EA
 EA --> ACK[Execution acknowledgement / fill / reject]
 ACK --> X
 X --> DB
 DB --> L[Outcome attribution and learning]
```

## Invariants
- All timestamps are timezone-aware at boundaries and stored in UTC.
- Every event has an idempotency key; duplicate delivery cannot duplicate side effects.
- Strategy signals never bypass an independent risk gate.
- Persist order intent before exposing it to the EA.
- Reconcile acknowledgements and fills against MT5 broker state.
- Telemetry backlog cannot starve execution-event processing.
- Redis is a recoverable buffer, not the durable source of truth.
- Missing/unhealthy risk policy fails closed.
