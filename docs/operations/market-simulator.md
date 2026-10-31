# Ramon Market Simulator

The standalone simulator sends reproducible synthetic XAUUSD data to the existing Django EA bridge. It is intended for weekend integration testing when the broker market is closed.

## Safety boundaries

- Uses the separate instance ID `RAMON-SIM-001` and account login `99999999`.
- Sends `terminal_trade_allowed=false` and `account_trade_mode=0` (synthetic demo identity).
- Sends telemetry and market events only. It never polls `/commands/next`, acknowledges commands, connects to MT5, or submits broker orders.
- Does not change command-creation, command-execution, or pipeline feature flags.
- Synthetic prices are not broker prices and must never be used to evaluate strategy profitability.

## Run locally

From the repository root, with the normal Compose stack running and `.env` containing `RAMON_EA_BEARER_TOKEN`:

```bash
python3 tools/market_simulator.py --once
python3 tools/market_simulator.py
python3 tools/market_simulator.py --interval 2 --decisions
```

Optional settings:

- `--base-url http://127.0.0.1:8017` changes the Django host.
- `--symbol XAUUSD_o` selects the symbol.
- `--start-price 2700` sets the initial synthetic price.
- `--seed 42` makes the generated history reproducible.
- `--decisions` calls the advisory decision endpoint once per newly completed M15 candle. It does not enable execution.

The script reads `RAMON_EA_BEARER_TOKEN` from the process environment or the selected `.env` file. Do not put the token on the command line.

## What is sent

- `POST /api/ea/v1/telemetry`: quote, synthetic account snapshot, and 128 completed M15 candles.
- `POST /api/ea/v1/events`: one `MARKET_TICK` event per simulator cycle.
- Optional `POST /api/ea/v1/decision`: one advisory decision request per new M15 candle.

The current Compose configuration keeps `RAMON_PIPELINE_INGEST_ENABLED=0`. In that configuration the `/events` endpoint stores events through the legacy Django database path; it does not enqueue them into Redis Streams. The simulator deliberately does not alter this rollout switch. Redis/worker pipeline testing requires an explicit, separately reviewed configuration change.

## Expected validation

After starting the simulator, check the EA bridge status endpoint and Django admin for instance `RAMON-SIM-001`. The telemetry response should report `ok=true`, `telemetry_only=true`, and `trading_authorization=false`. With `--decisions`, the decision endpoint may return `WAIT` because the synthetic account has no server-recorded daily-risk baseline; that is the intended fail-closed behavior, not a simulator failure.

Stop with Ctrl+C. No Docker volumes or database records are automatically deleted.
