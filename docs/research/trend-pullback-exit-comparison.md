# TREND_PULLBACK conditional exit comparison (research-only)

This branch adds `trend_pullback_exit_research` to the existing historical backtest JSON when TREND_PULLBACK is selected. It does **not** change the strategy worker, broker command producer, EA, live risk rules, account settings, or ARIMA observer.

## Variants
- `FIXED`: submitted SL and TP.
- `BREAKEVEN_1R`: after a completed M15 bar closes at least +1 initial R into profit, move SL toward entry, effective next candle.
- `ATR_TRAIL`: after +1R, trail by 1.5 x ATR(14) from each completed candle close; never widen SL.
- `HYBRID`: break-even plus ATR trailing, and a 32-candle time exit.

All variants reuse the baseline TREND_PULLBACK fills and initial levels. We evaluate each entry **independently** through future M15 bars. This is a *conditional per-entry exit experiment*, not a portfolio backtest: position overlap, changed availability for future entries, and account-equity-dependent sizing are not recalculated. Do not use `conditional_net_pnl` as projected account returns.

Conservative estimates: stop first when both levels occur within the same candle; adverse gap-through stop uses opening price; unknown path across missing M15 bars returns `DATA_GAP` rather than an invented exit; fixed modeled spread, per-side slippage and commission. Stop changes are effective only after a completed candle. Intrabar high/low paths, broker stop/freeze restrictions and historical variable costs are unknown. The current source history contains gaps and is descriptive-only, not strategy-validation-grade.

## Running and inspecting

On a deployed local research instance, use the existing Historical Simulator with only TREND_PULLBACK checked. Run a backtest and download its JSON. Look for `trend_pullback_exit_research.variants` and inspect per-entry `outcomes`, `unresolved_by_reason`, `mean_r`, and `modifications`.

Run the isolated unit tests with:

```sh
docker compose exec django python manage.py test ea_bridge.test_research_exit_comparison
```

Before considering live changes: obtain continuous validated M15 and M1 data from the same broker source, verify symbol specifications, compare costs and actual fills, implement full sequential replay (fresh entries/sizing after alternate exits), run frozen out-of-sample and walk-forward tests with sufficient trades, check drawdown and execution constraints, and separately approve any live policy.

ARIMA remains observation-only. Its small sample and near-identical error relative to the unchanged-price baseline currently do not support execution decisions.
