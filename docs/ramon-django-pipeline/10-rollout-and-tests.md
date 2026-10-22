# Rollout and test plan

1. Inventory legacy Ramon entry points, model calls, risk rules, EA protocol and stored data.
2. Land contracts/docs without changing the existing decision route.
3. Implement Django models/migrations and idempotent repository adapters.
4. Add Redis config, stream provisioning, consumer workers and lag metrics.
5. Port one strategy path behind a feature flag and compare outputs in shadow.
6. Test risk rejects, stale data, duplicates, retries, partial fills, restarts and reconciliation on demo.
7. Enable only a narrowly scoped execution path after evidence and explicit approval.
8. Remove legacy modules only in a separate reviewed change after parity tests.

Minimum coverage: schema and OHLCV tests, UTC tests, idempotency, fail-closed risk,
outbox transaction, stream retry/ack, EA protocol contract, and Postgres+Redis integration.
