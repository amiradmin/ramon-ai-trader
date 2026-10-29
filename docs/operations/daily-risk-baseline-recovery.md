# Ramon daily-risk baseline recovery

## Purpose

The Django decision API deliberately returns `WAIT / DAILY_LOSS_BASELINE_UNAVAILABLE` if it cannot find an opening-balance baseline for the exact broker account, server, and broker-local date. This is fail-closed behavior, not an indicator bug. Never substitute current equity or current balance for the opening balance.

## 1. Confirm the deployed code and migrations

Run from the repository root after pulling the reviewed code:

```bash
docker compose ps
docker compose exec -T django python manage.py showmigrations ea_bridge
docker compose exec -T django python manage.py migrate --check
```

The `0004_accountdailybaseline` migration must be marked `[X]`. If it is not, inspect the migration plan and then apply migrations with `python manage.py migrate`. Do not delete, reset, or recreate the PostgreSQL/TimescaleDB volumes.

## 2. Verify account identity and telemetry

The EA heartbeat and telemetry must agree on the exact account login, broker server, symbol, instance ID, broker-local date, balance, and equity. The latest telemetry must be fresh (the decision API's current guard is 90 seconds). The terminal's algorithmic-trading permission must be enabled only when intended; telemetry must never grant execution authorization.

The command EA sends its version as `ea_version`. Recompile and install the reviewed `mt5/RamonCommandEA.mq5` source to populate this field. Keep `EnableCommandExecution=false` during this diagnostic phase.

## 3. Create a baseline only from broker-verified opening balance

First obtain the account's actual opening balance for the broker-local day from the broker's account history/statement. Account for deposits, withdrawals, credit adjustments, and other balance operations. Do not guess from the current equity or balance.

Then run this command, replacing every placeholder with verified values:

```bash
docker compose exec -T django python manage.py capture_daily_baseline \
  --account-login '<BROKER_ACCOUNT_LOGIN>' \
  --server-name '<EXACT_BROKER_SERVER_NAME>' \
  --broker-day '<BROKER_LOCAL_DATE_YYYY-MM-DD>' \
  --opening-balance '<VERIFIED_OPENING_BALANCE>' \
  --source-instance-id '<CURRENT_EA_INSTANCE_ID>' \
  --confirm-broker-opening-balance
```

The command is intentionally explicit, refuses unconfirmed values, and never overwrites a baseline. If a baseline already exists, inspect it instead of trying to replace it. If the broker's true opening balance cannot be established reliably, leave the system blocked and investigate the broker history; do not force trading.

## 4. Validate decisions without enabling execution

Wait for a fresh decision request and inspect the latest `StrategyDecisionRecord`. A successful baseline should make `daily_risk.baseline_available=true` and provide the broker day, opening balance, equity, and server-derived daily-loss percentage. It does not guarantee a trade: market regime, entry timing, news, spread, open-position risk, stop loss, reward-to-risk, sizing, and terminal permissions remain independent gates.

Keep both `RAMON_EA_COMMAND_CREATION_ENABLED=0` and `RAMON_EA_COMMAND_EXECUTION_ENABLED=0` until end-to-end demo execution and reconciliation have been separately validated. A healthy dashboard or a passing decision test is not proof that broker execution is ready.
