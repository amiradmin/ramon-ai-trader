# Risk-aware strategy OPEN delivery (opt-in)

This is a **broker-risk safety feature**, not a signal generator or a retry mechanism.

The Django `commands/next` endpoint can optionally recalculate a previously approved STRATEGY OPEN order using a fresh scoped heartbeat quote immediately before claiming the command. With `RAMON_RISK_AWARE_DELIVERY_ENABLED=True`, it may **only lower volume**, to the broker's valid lot grid, while retaining direction, SL, TP, account/symbol, expiration and command ID. If even the broker's minimum lot exceeds the original approved risk budget, it rejects the command. It also rejects invalid metadata, unfavorable reward/risk, broker stop distance, or stale quote. No rejected order is automatically retried.

**Disabled by default**: deployment does not enable this feature or alter any existing automatic-trading switches. Explicit review and test are required before enabling it on an account. It applies only to strategy OPEN commands, not scalpers, MODIFY_SLTP, CLOSE or research/ARIMA.

The risk budget is at most the *original approved reserved risk* or 0.5% of the currently reported balance. All existing account identity, news and broker execution gates remain. Historical P&L cannot establish live execution safety. Broker stop/freeze limits, account-wide exposure and actual fills still require independent existing checks; do not interpret this calculation as a replacement for them.

## Verification

```bash
docker compose exec django python manage.py test ea_bridge.test_delivery_repricing ea_bridge.tests_commands ea_bridge.tests_strategy_workers
docker compose exec django python manage.py audit_trading_readiness --source ramon-command-ea-1 --symbol XAUUSD_l
```

Inspect rejection/claim events, current heartbeat and the actual `broker_ticket`/ack. The returned claim payload contains `delivery_repricing` for audit; no command is placed from unit tests. Compare observed changes to risk ceilings and lot steps for the **current** broker symbol; historical `XAUUSD_o` rejections do not prove the current `XAUUSD_l` setup has identical symbol specifications.

## Operator approval

After successful CI, controlled demo testing, and manual risk review, enable `RAMON_RISK_AWARE_DELIVERY_ENABLED=True` in Django settings only through your normal configuration management. Do not use a market order to test readiness. A feature enablement remains a separate operator action, never automatic on merge.

The exact historic code path that produced the older `DELIVERY_APPROVED_RISK_EXCEEDED` and `DELIVERY_PLAN_INVALID` results has not been reconstructed from those past runtime logs. This patch provides an additional Django-side safety gate at claim, not proof that the earlier rejection mechanism is fixed. Check broker/EA runtime source parity before rollout.
