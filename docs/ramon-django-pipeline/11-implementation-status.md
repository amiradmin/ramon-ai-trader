# Implementation status and rollout

## Current code added
- Django ORM models/migration for normalized market bars, decisions, risk verdicts and order intents.
- Optional Redis Streams ingress for the existing authenticated EA events endpoint.
- A management-command worker with consumer groups, stale pending-message reclaim, database transaction before ACK, idempotent writes and bounded retry/dead-letter handling.
- Redis AOF/no-eviction Compose service and separate worker service.
- Django endpoint tests for the default legacy path, opt-in queue path, Redis outage and timestamp validation.

## Rollout controls
- `RAMON_PIPELINE_INGEST_ENABLED` defaults to `0`; current API behavior is preserved by default.
- In queue mode, Redis acceptance is acknowledged to the EA; DB failures are retried by the worker and eventually quarantined for inspection.
- The worker does not send commands to MT5 and does not make trade decisions.
- The order-intent model is a durable schema only; no command-polling endpoint is enabled.

## Validation still required
Run Django checks, migration consistency checks and the test suite in the repository environment. Then run integration tests against the actual Redis and database configuration, test Redis/DB restarts and inspect dead-letter behavior. Keep the ingestion flag off until these checks pass. Do not enable live trading based on these changes alone.
