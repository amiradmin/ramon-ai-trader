# Optional timing instrumentation (no trading logic changes)

Both flags default off. Enabling them in a future controlled
deployment will emit structured log lines:

- `RAMON_DECISION_TIMING_ENABLED=1`: `RAMON_DECISION_TIMING status=200 duration_ms=...`
- `RAMON_STORAGE_TIMING_ENABLED=1`: `RAMON_STORAGE_TIMING stage=sqlite status=ok duration_ms=...`
- When the PostgreSQL M15 shadow is separately enabled, its elapsed time
  is reported with `stage=postgres_shadow`.

No DSN, password or raw price payload is logged. SQLite stays authoritative.
The storage measurements are per-write, and the decision timer covers
the POST processing up to response preparation, not the network send.
Errors on the optional PostgreSQL shadow do not prevent the response.

**Warning:** The PostgreSQL shadow writer is currently synchronous. These
metrics make its latency visible but do not prevent a slow PostgreSQL
connection from delaying a live decision. Shadow should stay off until a
non-blocking writer and performance gates are validated.

## Unit tests on HP-Mini

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync python -m pytest -q \
  tests/test_storage_timing.py tests/test_postgres_market_shadow.py
```

Do not rebuild/restart the live model only to test these changes.
This PR only adds instrumentation, not an activated telemetry setting.
