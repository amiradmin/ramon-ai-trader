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

## Timestamp root cause and fail-closed ingestion

The MT5 command EA previously calculated `TimeTradeServer() - TimeGMT()` using two independent reads. Small sampling drift could shift every converted candle by a few seconds. The producer now rounds only a broker/GMT offset within 120 seconds of an exact 15-minute timezone boundary; an implausible offset stops that upload. **Recompile and reinstall the updated `mt5/RamonCommandEA.mq5`** before expecting improvement in incoming data. This does not reconstruct the correct historical UTC offset around earlier DST transitions, nor does it fix already-ingested rows.

The Redis persistence worker now rejects off-grid M1/M5/M15/M30/H1/H4 candle timestamps into `EAEvent` entries labeled `MARKET_BAR_REJECTED` with reason `OFF_GRID_TIMESTAMP` and the original event. These are retained and acknowledged only after commit, so malformed source rows do not poison study data or block the queue. Rebuild **worker** as well as django after pulling.

## Replay/runtime configuration parity

`evaluate_demo_playbook` now defaults to `--profile standard` (the standard threshold profile with demo-only detectors disabled). Use `--profile expanded` only for separately labeled research. The JSON output contains effective policy profiles, a SHA-256 fingerprint, fixed replay cost assumptions, and the tested profile. A matched settings hash is **necessary, not sufficient** for execution parity: the MT5 EA version, active account, account permissions, spreads, symbol contract, and execution-specific guards must also match.

For an explicitly frozen replay snapshot (save the full output, not just the hash):

```bash
docker compose exec -T django python manage.py evaluate_demo_playbook \
  --profile standard --source YOUR_EA_INSTANCE_ID --symbol XAUUSD_o \
  --spread-points 22 --slippage-points 5 --commission-points 5 \
  > replay-standard-policy.json
docker compose exec -T django python manage.py audit_trading_readiness \
  --source YOUR_EA_INSTANCE_ID --symbol XAUUSD_o --strict \
  --expected-config /path/inside/container/replay-standard-policy.json
```

The example costs are **illustrative**, not verified historical broker costs. `--expected-config` must be accessible **inside** the container; a redirected host file isn't automatically visible there. For example, copy the report into the container using `docker compose cp replay-standard-policy.json django:/tmp/replay-standard-policy.json` and pass that path. Keep the frozen report unchanged for later deployment checks. Compare `profiles` and the detected mismatches, not merely a saved hash.

Do **not** enable command creation/execution on the basis of this check. The standard replay is still descriptive/in-sample, and the fixed-cost candle simulation is not an executable-tick reconstruction.

Replay now **fails closed** when any M15, M5 or H1 input is off-grid. After diagnosing legacy data, use `--allow-off-grid` only for an explicitly labeled descriptive diagnostic; that override excludes invalid rows without altering the database. Do not treat an overridden replay as clean walk-forward evidence. The frozen report includes per-timeframe exclusion counts.
