# Demo-only trading activation

This runbook covers the guarded Django-to-MT5 command path. It does not guarantee that a trade will occur: Ramon must receive fresh market/account telemetry and produce a valid directional signal that passes all entry, risk, daily-loss, spread, stop/target, and broker checks.

## Safety contract

- Django remains the sole strategy and risk decision-maker.
- Missing/stale telemetry, missing daily baseline, an open position whose risk cannot be verified, or an incomplete risk verdict must result in `WAIT` / no command.
- The server creates an OPEN command only when both `RAMON_EA_COMMAND_CREATION_ENABLED=1` and `RAMON_EA_COMMAND_EXECUTION_ENABLED=1`.
- The server additionally requires explicit broker telemetry `account_trade_mode=0` (MT5 demo). Missing, malformed, contest, or real-account mode is rejected.
- The EA must have `EnableCommandExecution=true` for execution, and its own `SafeOpen` guard independently rejects non-demo accounts, disabled terminal/EA trading, invalid volume, wide spread, invalid SL/TP, and per-order loss above `MaxRiskMoney`.
- The committed EA input default remains `EnableCommandExecution=false`; never use this workflow to enable live-account trading.

## 1. Verify deployment and schema

From the project directory:

```bash
docker compose ps
docker compose exec -T django python manage.py check
docker compose exec -T django python manage.py showmigrations ea_bridge
docker compose exec -T django python manage.py migrate --check
```

Confirm all four services are healthy and migration `0004_accountdailybaseline` is applied. Do not delete or recreate PostgreSQL/Redis volumes to resolve migration problems.

## 2. Install the matching EA source

1. Copy `mt5/RamonCommandEA_v1.02.mq5` into the active MT5 terminal's `MQL5/Experts` directory.
2. Compile it in MetaEditor and confirm zero errors and zero warnings. Verify that the resulting `RamonCommandEA_v1.02.ex5` is the binary compiled from this exact source.
3. Attach it only to the intended demo broker account and the broker's gold symbol (for example `XAUUSD_o`) on an M15 chart.
4. Add `http://127.0.0.1:8017` to MT5's allowed WebRequest URLs.
5. Set `DjangoBearerToken` in the EA Inputs locally. Do not commit the token.
6. Initially leave `EnableCommandExecution=false`. Confirm the Experts log shows v1.02, telemetry requests succeed, and decision polling reaches Django.

The EA must send the exact broker server name, broker-local date, account trade mode, balance/equity, quote timestamp, and positions. Confirm the dashboard/heartbeat shows the intended account and `account_trade_mode=0`.

## 3. Establish today's daily-risk baseline

A missing baseline intentionally blocks entries. Never substitute the current balance or current equity for the opening balance.

Check whether today's `AccountDailyBaseline` exists for the exact account login, exact server name, and broker-local date. If it does not, use the broker's account history/statement to verify the opening balance for that broker day, adjusting for deposits, withdrawals, and credit operations where applicable. Then run:

```bash
docker compose exec -T django python manage.py capture_daily_baseline \
  --account-login '<BROKER_ACCOUNT_LOGIN>' \
  --server-name '<EXACT_BROKER_SERVER_NAME>' \
  --broker-day '<BROKER_LOCAL_DATE_YYYY-MM-DD>' \
  --opening-balance '<VERIFIED_OPENING_BALANCE>' \
  --source-instance-id 'ramon-command-ea-1' \
  --confirm-broker-opening-balance
```

Replace every placeholder with verified values. The command refuses to overwrite an existing baseline. If you cannot verify the broker opening balance, do not create one; leave Ramon in WAIT mode.

## 4. Enable demo-only command delivery deliberately

Only after the above checks pass, edit the local `.env` (never commit it) and set:

```dotenv
RAMON_EA_COMMAND_CREATION_ENABLED=1
RAMON_EA_COMMAND_EXECUTION_ENABLED=1
```

Recreate only Django so it reads the new environment:

```bash
docker compose up -d --build --force-recreate django
docker compose ps
```

Do not use `down -v`, remove volumes, or delete database data. In the EA Inputs, set `EnableCommandExecution=true` only while connected to the intended demo account. The server-side `account_trade_mode=0` check and EA-side demo-only guard are both mandatory; if either disagrees, no order should be placed.

## 5. Verify a complete order lifecycle

Watch the Experts log and Django logs:

```bash
docker compose logs --since=10m django
```

A successful path should be traceable through:
1. Fresh telemetry heartbeat for the expected instance/account/server/symbol.
2. A Django decision with a directional `BUY` or `SELL` action and all risk gates approved.
3. An approved risk verdict with positive volume, valid protective stop and target, and acceptable reward/risk.
4. A single unexpired OPEN command scoped to the exact instance/account/symbol.
5. EA command receipt and local demo/symbol/spread/volume/risk checks.
6. Broker order result and command acknowledgement, followed by a refreshed telemetry snapshot showing the position.

If any step fails, use the returned reason code and fix that prerequisite. Do not lower risk limits, fabricate a signal, or disable a guard to force a trade. Repeated requests for the same decision must not create duplicate commands.

## Acceptance checklist

- [ ] Django health check and migrations pass.
- [ ] Exact EA source compiled with zero errors/warnings and installed in the active terminal.
- [ ] Live telemetry reports `ea_version=1.02` and integer `account_trade_mode=0`.
- [ ] Daily baseline exists for the exact account/server/broker day and is broker-verified.
- [ ] Missing baseline and non-demo account tests fail closed.
- [ ] Both server command flags and EA execution input are enabled only for the intended demo test.
- [ ] A real strategy/risk-approved demo signal creates one command and the EA acknowledges the broker result.
- [ ] Position appears in MT5 and is subsequently reflected in Django telemetry.
- [ ] If conditions never become true, no order is expected; that is not a reason to bypass the strategy or risk gates.
