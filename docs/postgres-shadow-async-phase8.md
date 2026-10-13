# Phase 8 — Optional bounded asynchronous PostgreSQL M15 Shadow

This PR introduces a daemon worker and a bounded 32-item in-memory queue for
PostgreSQL M15 shadow writes after SQLite succeeds. It **does not** activate
shadow writes, change Docker Compose, or change EA trading logic.

Flags (both default unset):
- `RAMON_PG_MARKET_SHADOW_DSN`: enables existing PostgreSQL shadow.
- `RAMON_PG_SHADOW_ASYNC_ENABLED=1`: sends enabled shadow writes to the
  worker instead of synchronously writing in the request.

**Do not enable live yet.** Until the old synchronous path is removed,
setting only the DSN still allows synchronous network waits. The new
queue is best-effort, not durable: it drops jobs when full and loses
pending items on container shutdown/restart. Drop/failure warnings are logged
without credentials. SQLite remains the source of truth. Run parity
and freshness checks and plan a repair/reconciliation flow before rollout.

The queue does not guarantee every completed bar will be mirrored.
A nonblocking queue avoids waiting for PostgreSQL from the normal decision
handler, but thread initialization and putting jobs still use tiny amounts
of local CPU and memory.

## HP-Mini tests (no database changes)

```bash
git fetch origin
git switch migration/postgres-shadow-async-queue
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync python -m pytest -q \
 tests/test_postgres_shadow_queue.py tests/test_storage_timing.py \
 tests/test_postgres_market_shadow.py
```

Keep shadow disabled and do not rebuild running model just to run these tests.
