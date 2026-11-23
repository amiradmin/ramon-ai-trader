# TREND_PULLBACK isolated parameter sweep

This is an **offline research-only** workflow. It never calls the live Django decision endpoint, command producer, EA execution APIs, or changes database strategy settings. It does **not** enable or force any live trade.

## Run

First import and audit a valid M15 `MarketBar` source. Use the same exact source, broker assumptions and chronological periods for every candidate. The included frozen research assumptions reference the November 23 XAUUSD_l preset, but **the source must be supplied explicitly** and must exist locally.

```bash
docker compose exec django python manage.py research_trend_pullback_sweep \
  --source litefinance-mt5-live-export-20261122 \
  --start 2026-09-01 \
  --holdout-start 2026-10-15 \
  --end 2026-11-20
```

**Dates above are examples**, not a claim the source has complete history. Each period requires 128+ historical warm-up candles and fits the 16,000-candle limit. If the history does not cover these dates, choose validated intervals actually contained in your source. The report is saved to `MEDIA_ROOT/trend-pullback-sweeps/sweep-*.json`. The command prints each baseline/candidate result, including signals, closed trades, modeled net gain, drawdown and historical data quality.

The seven profiles are `baseline`, `earlier_impulse`, `shallower_pullback`, `deeper_pullback`, `shorter_regime`, `lower_volatility`, `combined_experiment`. You may select a subset with `--profiles baseline earlier_impulse shallower_pullback`.

Experimental parameters only apply to the freshly constructed research pipeline:

| Candidate | Override |
|---|---|
| earlier_impulse | min_impulse_atr = 1.25 |
| shallower_pullback | min_retracement = 0.15 |
| deeper_pullback | max_retracement = 0.88 |
| shorter_regime | regime_lookback = 5 |
| lower_volatility | atr_ratio_min = 0.50 |
| combined_experiment | all five |

All other config values and the standard risk cap are unchanged. The frozen preset provides point/tick value, spread, stop and target assumptions. These are not evidence of historical fill conditions, and they may differ from actual broker behavior.

## Interpreting results

Compare to the **baseline separately in development and untouched holdout periods**. Do not select parameters based on the holdout and then claim the same holdout is independent. Demand a reasonable number of independent trades; results with zero/few closed trades cannot establish positive expectancy. Reported profit factor is `null` when there are no losses. Review missing/off-grid bars, raw signal/rejection counts, slippage, spread and commission assumptions; a history quality warning invalidates a confident performance conclusion.

The simulator fills at the next candle open, assumes fixed modeled costs, and places a conservative stop first when intrabar stop and target both appear hit. It does not reconstruct broker tick order, news gate history, portfolio exposure, actual order rejection, or the live MT5 execution path. No candidate should be promoted to live without additional verified data, walk-forward tests and paper-forward testing.

## Production isolation

`research_overrides` exists only as an optional **internal argument** of the replay pipeline; it is not accepted by the live settings endpoint or the public backtest request parser. The experiment does not alter `DecisionThresholdConfig`, `StrategyWorkerControl`, `RAMON_EA_COMMAND_CREATION_ENABLED`, or `RAMON_EA_COMMAND_EXECUTION_ENABLED`.

To use another date range, rerun the command. Keep previous reports for comparisons by source/history fingerprint. No automatic promotion to live is provided.
