# Reversal by hour and market-condition proxy

Read-only research on completed canonical M1/M5 snapshots, evaluated only on
chronological forward windows. Uses the existing Reversal signal. Breaks down
results by UTC hour and a **proxy regime** calculated from the past 16 completed
M1 closes:

* TREND_PROXY: directional efficiency >= 0.55
* RANGE_PROXY: directional efficiency <= 0.25
* TRANSITION_PROXY: everything between them
* FLAT/UNKNOWN when measurements are unavailable.

These categories are NOT the production Ramon market-state model. Do not
interpret them as production market-state annotations. Hour is UTC, not Tehran
or exchange local time.

```bash
git pull --ff-only origin main
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -q tests/test_reversal_regime_audit.py
PYTHONPATH=src uv run python -m ramon.reversal_regime_audit \
  --db data/ramon_history.sqlite3 \
  --symbol XAUUSD_l --folds 4 --horizon-minutes 5 \
  --point-size 0.01 --spread-price 0.42 --slippage-price 0.10
```

Small groups (<30 observations) are marked `sufficient_sample=false`.
When fewer than five separate UTC trading dates are represented, the report
marks `INSUFFICIENT_INDEPENDENT_DAYS`. Even after five days,
`promotion_ready=false` by design. It does not estimate real account PnL
or allow any live trade. Collect several non-consecutive market conditions;
then build an execution-aware validation before considering policy changes.

Known limitations: historical source may repeat/roll files; assumed spread
points/point size are not quote fills; hourly slicing is descriptive and
prone to multiple-comparison selection bias; this report does not implement
real broker stop-loss/target, latency, tick replay, commission, or drawdown.
No changes were made to Ramon EA, manual execution, or live WAIT gates.
