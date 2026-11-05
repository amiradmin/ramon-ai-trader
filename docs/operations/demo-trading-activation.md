# Demo-only trading activation

This runbook covers the guarded Django-to-MT5 command path. A trade is expected only when fresh market/account telemetry produces a directional setup that passes Opportunity Finder, Entry Timing, Risk Management, daily-loss, account, and broker checks.

## Safety contract

- Django is the sole strategy and risk decision-maker.
- The server creates an OPEN command only when both `RAMON_EA_COMMAND_CREATION_ENABLED=1` and `RAMON_EA_COMMAND_EXECUTION_ENABLED=1`.
- Django requires explicit broker telemetry `account_trade_mode=0`, fresh heartbeat/quote, matching instance/account/server/symbol, verified risk inputs, and no existing position whose risk is unverified.
- Django validates the connected account is demo, exact instance/account/server/symbol scope, command expiry, server authorization, and terminal trading permissions before delivering a command. The EA does not repeat these checks.
- The EA does not calculate signals, position size, daily loss, portfolio risk, or risk thresholds. Those remain Django-owned.
- The EA acknowledges `EXECUTED` for OPEN only after the broker returns `TRADE_RETCODE_DONE` and provides an order/deal identifier. Partial fills, merely placed orders, and ambiguous results are `RECONCILIATION_REQUIRED`; they are never blindly retried.
- Never enable command gates for a real account.

## 1. Verify deployment and schema

```bash
docker compose ps
docker compose exec -T django python manage.py check
docker compose exec -T django python manage.py showmigrations ea_bridge
docker compose exec -T django python manage.py migrate --check
```

Do not delete or recreate PostgreSQL/Redis volumes to resolve migration problems.

## 2. Install the matching EA source

1. Confirm the intended LiteFinance demo login, exact broker server, and `XAUUSD_o` M15 chart.
2. Run `bash scripts/update_build_ramon.sh` from the repository directory. It fast-forwards main, rebuilds the stack, compiles and installs only `RamonCommandEA_v1.07.mq5`, and backs up the previous EA source/binary.
3. Confirm MetaEditor reports exactly zero errors and zero warnings.
4. Set `DjangoBearerToken` only in the local EA Inputs. Never commit the token.
5. Confirm the Experts log reports v1.07 and telemetry/decision requests succeed.

## 3. Verify the daily-risk baseline

A verified broker-day opening balance is preferred. Real accounts fail closed without a verified baseline. Demo accounts may use only the explicitly audited `DEMO_NO_BASELINE_BYPASS` path when no verified server or broker-history baseline is available; daily-loss enforcement is unavailable in that case and must remain visible in the audit record.

Never fabricate an opening balance or substitute current balance/equity. If manually capturing a baseline, verify it against the broker statement and use the `capture_daily_baseline` management command with the exact login, server, broker-local date, and verified opening balance.

## 4. Enable command delivery deliberately

Only after the preceding checks pass, edit the local `.env` (never commit it):

```dotenv
RAMON_EA_COMMAND_CREATION_ENABLED=1
RAMON_EA_COMMAND_EXECUTION_ENABLED=1
```

Recreate only Django so it reads the environment:

```bash
docker compose up -d --build --force-recreate django
docker compose ps
```

Django rejects non-demo accounts and mismatched broker servers before command delivery. The two Django command gates are the operator-controlled execution switch. Do not lower strategy or risk thresholds to force a trade.

## 5. Verify a complete order lifecycle

Watch the Experts log and Django logs:

```bash
docker compose logs --since=10m django
```

A successful path must show:
1. Fresh telemetry for the expected EA instance/account/server/symbol.
2. A directional BUY/SELL decision with all three SignalFlow stages approved.
3. An approved risk verdict with positive volume and valid SL/TP.
4. One unexpired OPEN command scoped to the exact account and broker server.
5. EA dispatches the command already authorized by Django; it does not validate trading conditions or account policy.
6. EA reports the broker retcode and order/deal identifier to Django.
7. Django acknowledgement as `EXECUTED`, followed by a refreshed telemetry snapshot showing the open position.

Partial fills, merely placed orders, missing fill identifiers, and uncertain outcomes must be reconciled before any further action. A healthy API or passing CI is not proof of broker execution.

## Acceptance checklist

- [ ] Django health check and migrations pass.
- [ ] EA v1.07 compiles with zero errors and warnings and is installed in the active terminal.
- [ ] Telemetry reports the intended account, exact broker server, and integer `account_trade_mode=0`.
- [ ] Both server command gates are enabled only for the intended demo test.
- [ ] A genuine strategy/risk-approved signal creates one account-scoped OPEN command.
- [ ] Django checks demo mode and exact broker-server/account/symbol scope before command delivery.
- [ ] EA reports the broker retcode; Django reconciles the result and records execution status.
- [ ] The resulting position appears in MT5 and in subsequent Django telemetry.
- [ ] Ambiguous broker outcomes are marked `RECONCILIATION_REQUIRED` and are not retried blindly.
- [ ] No order is created when any required strategy, risk, account, quote, or execution condition fails.
