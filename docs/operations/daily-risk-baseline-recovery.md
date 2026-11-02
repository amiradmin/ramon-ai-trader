# Ramon daily-risk baseline recovery

## Purpose

The Django decision API deliberately returns `WAIT / DAILY_LOSS_BASELINE_UNAVAILABLE` when it cannot find an opening-balance baseline for the exact broker account, server, and broker-local date. This is fail-closed behavior, not an indicator bug. Current equity or current balance alone is never an acceptable substitute.

## 1. Confirm the deployed code and migrations

Run from the repository root after pulling the reviewed code:

```bash
docker compose ps
docker compose exec -T django python manage.py showmigrations ea_bridge
docker compose exec -T django python manage.py migrate --check
```

The `0005_accountdailybaseline_capture_method` migration must be marked `[X]`. Do not delete, reset, or recreate PostgreSQL/TimescaleDB volumes.

## 2. Let the updated EA derive the opening balance from broker history

Use `RamonCommandEA_v1.04.mq5` on the intended demo account. Keep `EnableCommandExecution=false`.

On each telemetry cycle, the EA selects broker deal history from broker-local midnight through the current broker-server time and computes:

```text
opening balance = current account balance
                - sum(deal profit + swap + commission + fee)
```

The EA marks history complete only when history selection and every required deal-property read succeeds and the resulting opening balance is finite and positive. Django validates the explicit completeness flag, amount, and deal count before creating an immutable `BROKER_HISTORY_DERIVED` baseline. This allows first-time attachment after midnight and does not depend on observing the rollover.

If broker history is unavailable or invalid, the EA sends telemetry without a complete-history assertion. Django retains the existing continuous-rollover fallback (same account/server, consecutive broker dates, and no more than 90 seconds between observations). Otherwise no baseline is created and decisions remain `WAIT`.

## 3. Verify the baseline and its provenance

After the updated EA is attached and at least one telemetry cycle has completed, inspect the baseline:

```bash
docker compose exec -T django python manage.py shell -c '
from ea_bridge.models import AccountDailyBaseline
for row in AccountDailyBaseline.objects.order_by("-broker_day")[:10]:
    print(row.account_login, row.server_name, row.broker_day,
          row.opening_balance, row.capture_method, row.source_instance_id)
'
```

Expected capture methods are:

- `BROKER_HISTORY_DERIVED` — computed from complete broker deal history by the EA.
- `ROLLOVER_CONTINUITY` — previous day's balance captured across a fresh, continuous rollover.
- `MANUAL_BROKER_VERIFIED` — explicitly entered after checking the broker statement/history.

Baselines are immutable. If a row already exists, inspect it; do not overwrite it automatically.

## 4. Manual fallback only when broker history cannot be read

Obtain the actual opening balance for the broker-local day from the broker's account history/statement. Account for deposits, withdrawals, credit adjustments, commissions, swaps, fees, and other balance operations. Do not guess from current equity or balance.

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

The command refuses unconfirmed values and never overwrites a baseline.

## 5. Validate decisions without enabling execution

Wait for fresh telemetry and a decision request. Inspect the latest `StrategyDecisionRecord` and confirm `daily_risk.baseline_available=true`, the exact broker day, opening balance, current equity, capture method, and server-derived daily-loss percentage.

A valid baseline does not guarantee a trade: market regime, entry timing, news, spread, open-position risk, stop loss, reward-to-risk, sizing, and terminal permissions remain independent gates.

Keep both `RAMON_EA_COMMAND_CREATION_ENABLED=0` and `RAMON_EA_COMMAND_EXECUTION_ENABLED=0` until end-to-end demo execution and reconciliation have been separately validated. A healthy dashboard or a passing decision test is not proof that broker execution is ready.
