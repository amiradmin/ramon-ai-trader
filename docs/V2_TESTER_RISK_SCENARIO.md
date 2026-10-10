# V2 — deterministic MT5 tester-only portfolio-risk scenario

This harness is emitted **only** into generated `RamonTester.mq5` by
`scripts/generate_ramon_tester.py`. The production `mt5/Ramon.mq5`
and `Ramon.ex5` are not modified.

## What it does

- First risk is supplied as a **synthetic** historical SELL exposure,
  287.82 account units, representing one of the 8 October trades.
- It calls the actual `V2Preflight()` from the EA with a broker-derived
  minimum-lot SELL candidate, generated SL geometry, and injected prior
  risk. The candidate is **not** forced to be the historical 292.50
  account-unit second order: the log prints both the historical target
  and the *actual candidate* computed by `OrderCalcProfit`.
- With a test-only account cap of **3.00 USD** and configured conversion
  **100 account units/USD**, it should reject the new risk by the
  `portfolio risk cap` branch.
- It logs `RAMON_TEST_RISK PASS`, `FAIL`, or `INCONCLUSIVE`
  for reviewer inspection. **No orders are placed by the harness.**
- The harness runs once in `OnTick`, only when `MQL_TESTER` is true,
  `EnableLiveTrading=false` and `TesterRunRiskHarness=true`.
- The hook is off by default, and generator validation refuses an
  unexpected production source layout.

## Rebuild and run on HP-Mini

Keep live Algo Trading disabled, check for open real positions and keep
protective SL/TP orders intact.

```bash
cd ~/Documents/Presentation/ramon-ai-trader
git status --short
git pull --ff-only
PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_generate_ramon_tester.py
python3 scripts/generate_ramon_tester.py

TESTER="$HOME/.mt5/drive_c/Program Files/MetaTrader 5/MQL5/Experts/RamonTester"
cp -v build/mt5-tester/RamonTester.mq5 "$TESTER/"
grep -n "TesterRunRiskHarness\|WriteDiagnosticFile" "$TESTER/RamonTester.mq5"
```

Recompile **only `RamonTester.mq5`** in MetaEditor with F7,
0 errors and 0 warnings. Check that `RamonTester.ex5` is newer
than `RamonTester.mq5`.

In Strategy Tester use `RamonTester/RamonTester.ex5` and `XAUUSD_l M15`,
real ticks, dates Oct 7–9 2026. In **Inputs** set:

| Input | Value |
|---|---|
| `EnableLiveTrading` | `false` |
| `EnableV2AccountRiskGuard` | `true` |
| `WriteDiagnosticFile` | `false` |
| `TesterRunRiskHarness` | `true` |
| `V2MaxPortfolioRiskUSD` | `3.0` (TEST ONLY) |
| `V2MaxSymbolRiskUSD` | `3.0` (TEST ONLY) |
| `V2MaxDirectionRiskUSD` | `3.0` (TEST ONLY) |
| `MoneyUnitsPerUSD` | `100.0` (historical cent unit convention) |

**Keep the real-production defaults at USD 0.35**; 3.0 is used solely
to recreate the prior historical risk cap in the test terminal.
The tester deposit and contract specifications may not match the live
cent account. If `RAMON_TEST_RISK INCONCLUSIVE` appears, do not
interpret it as pass; capture the complete log line.

After run, use Journal search `RAMON_TEST_RISK` and provide the result.

## Scope & remaining work

This is a deterministic **gating test** using the real MQL5 risk
preflight and the terminal's broker contract calculations. It does
NOT verify actual execution of two positions, Guardian lifecycle,
concurrent EA races, account Stop Out recovery, or Chronos over HTTP.

A full V2 backtest still requires recorded AI decision playback through
the EA's execution pipeline in MT5 Strategy Tester, using tester-local
data rather than `WebRequest`. Promotion to live trading remains
blocked until that is demonstrated on demo and reviewed.
