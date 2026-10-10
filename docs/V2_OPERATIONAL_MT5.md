# Ramon V2 — Operational MT5 execution observability

The live Ramon **V2** decision engine remains the active engine. This rollout
adds a Docker-managed, read-only **execution-quality worker** to the existing
EA -> trade-outbox -> model `/trades` -> SQLite pipeline.

## Operational path

```text
Ramon.mq5 -> TradeOutbox -> existing /trades HTTP handler -> trade_outcomes
                                                     |
                                              v2-execution-health
                                                     |
                                      data/v2_mt5_execution_health.json
```

The worker publishes a new JSON report every 30 seconds, calculates net
account units, mean net R, profit factor, win rate, exit reasons, censored
manual labels, cost telemetry coverage, and fill-price telemetry coverage.
Reports explicitly warn when costs or fills are missing. No account-currency
conversion is assumed and no actual order is sent by this worker.

## Deploy in the existing Ubuntu/Docker setup

Keep local changes safe before switching to the PR branch. Do NOT run
`git reset --hard` or `git clean -fd`.

```bash
git fetch origin
git switch feature/v3-metatrader-eight-integrations
docker compose build model
docker compose up -d model trade-outbox v2-execution-health
docker compose ps
docker compose logs --tail=30 v2-execution-health
cat data/v2_mt5_execution_health.json
```

To run without daemon:

```bash
docker compose --profile tools run --rm --no-deps tools \
  -m ramon.v2_mt5_execution_health --db /data/ramon_history.sqlite3 \
  --output /data/v2_mt5_execution_health.json --once
```

Tests (host environment with dev dependencies):

```bash
PYTHONPATH=src uv run --extra dev pytest -q \
  tests/test_v2_mt5_execution_health.py tests/test_v3_mt5_integrations.py
```

If the report shows `database_not_found`, verify the model is persisting
`/trades` into the mounted history database. A report containing
`no_closed_trades` does NOT certify EA telemetry health. The worker reports
observed trades only, never fills gaps with synthetic data.

## Eight MT5 feature tracks

- **Operational now after deployment**: V2 execution/learning feedback
  telemetry audit with existing MT5 outbox, cost-coverage report,
  deduplicated optional transaction journal schema, local optimization and
  as-of replay helpers.
- **Partially available**: existing ForexFactory guard plus MT5 calendar
  normalizer (not native MT5 Calendar transport); NumPy/OpenBLAS detection
  (not MQL5 native BLAS); optional Python package probe.
- **Not connected to a live terminal yet**: MCP read-only server, native
  Strategy Tester agent or real-tick EA replay, MQL5 OnTradeTransaction
  event producer, inline ONNX runtime and distributed optimization agents.

Never infer that a prerequisite-detected flag equals an active MT5 connection.

## Live trading safety

No change to EnableLiveTrading, execution budgets, risk caps, V2 direction
gates, V3 shadow routing or broker orders. A successful telemetry deployment
is not evidence of positive edge or readiness for larger USD positions.
