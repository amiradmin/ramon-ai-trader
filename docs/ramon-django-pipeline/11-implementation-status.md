# Implementation status and rollout

## Current code added
- Django ORM models/migration for normalized market bars, decisions, risk verdicts and order intents.
- Optional Redis Streams ingress for the existing authenticated EA events endpoint.
- A management-command worker with consumer groups, stale pending-message reclaim, transaction-before-ACK, idempotent writes, bounded retry and dead-letter handling.
- Redis AOF/no-eviction Compose service and a worker service.
- A transactional decision orchestrator that persists strategy decision, independent risk verdict and deterministic order intent; it fails closed when risk policy is absent.
- Endpoint, worker persistence and orchestration tests.
- GitHub Actions checks for Django system checks, migration consistency and tests.

## Rollout controls
- `RAMON_PIPELINE_INGEST_ENABLED` defaults to `0`; existing API behavior is preserved by default.
- In queue mode, Redis acceptance is acknowledged to the EA; DB failures are retried by the worker and eventually quarantined for inspection.
- The worker does not send commands to MT5 and does not make strategy decisions.
- The orchestration service does not provide a strategy implementation and does not deliver order intents to the EA.
- The order-intent model is a durable schema only; no command-polling endpoint is enabled.

## Validation
GitHub Actions is configured to run `manage.py check`, `makemigrations --check --dry-run`, and `manage.py test`. Keep the ingestion flag off until those checks and Redis/database integration tests pass. Do not enable live trading based on these changes alone.
