# P0 safety and data-integrity rollout (2026-11-19)

This change does **not** prove a profitable strategy or activate any trading permission.

## What has changed

- The Django SignalFlow pipeline clamps the configured per-trade risk ceiling to **0.5% of balance** for both standard and demo profiles. Older saved dashboard values above 0.5% cannot override this code-owned cap. Other execution paths and already-open positions must be audited separately.
- The existing `RAMON_DEMO_PROFIT_PROTECTION_ENABLED=0` default is unchanged. Check the deployed `.env` and turn this flag off explicitly if the old +1R breakeven modification is still enabled.
- A new **read-only** management command reports effective Django SignalFlow settings and alignment/duplicate counts from stored market bars. It does not modify historical data or assume off-grid candles can safely be rounded.

## Run in your local checkout

```bash
git pull --ff-only origin main
docker compose up -d --build django
docker compose exec django python manage.py check
docker compose exec django python manage.py test ea_bridge.test_demo_risk_policy
docker compose exec django python manage.py audit_trading_readiness --source YOUR_EA_INSTANCE_ID --symbol XAUUSD_o --limit 20000
```

Add `--strict` to fail when bars are off-grid or the runtime risk ceiling violates (0, 0.5]. The report examines only the selected source/symbol and the most recent N bars *across* timeframes. It does not inspect the MT5 terminal's environment or broker-side execution settings.

## Checks still required before trading

1. Compare the audit's `standard` and `active_demo` settings with the **exact** replay snapshot and installed EA. In particular check `RAMON_SHORT_STOP_SPREAD_ADJUST`, stop buffer and min/max stop ATR, spreads, and the selected profile. Set `RAMON_DEMO_PROFILE=standard` to validate the standard setup under the demo account, but do not treat that as authorization.
2. Investigate timestamps with seconds offset from the expected timeframe boundary at the EA producer and ingestion path. Existing off-grid data must be traced to their source; indiscriminately flooring timestamps can merge distinct candles.
3. Re-run a genuinely chronological multi-year walk-forward test on the **chosen** profile with broker spreads, commissions and slippage. Record outcomes per year, session and detector; retain no-trade controls and out-of-sample distinctions.
4. Keep command creation/execution disabled until reconciling the account, demonstrated strategy evidence, and broker-side stops. A live .env risk flag or an unrelated strategy/execution engine may have separate limits.
