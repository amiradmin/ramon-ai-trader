# Entry Timing v2 — offline research only

The current live M1/M5 agreement indicator has a negative initial five-minute
closed-price proxy. This experiment compares four **simple**, deterministic
signals on the same canonical UTC bar set:

- `momentum`: follow the last three M1 close-to-close moves
- `mean_reversion`: fade that short-term move
- `reversal`: act only when latest M1 move contradicts prior short-term move
- `mtf_agreement`: require M1 and prior completed M5 momentum agreement

It uses a chronological 70/30 train/holdout split, a five-minute gap between
train outcome and test start, no overlapping positions within a strategy/split,
only completed candles, and an explicit spread and slippage assumption.
Selection, if any, is performed ONLY on the train portion. Holdout performance
is reported without making any changes to live entry authorization.

Run:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -q tests/test_entry_timing_v2_lab.py
PYTHONPATH=src uv run python -m ramon.entry_timing_v2_lab \
  --db data/ramon_history.sqlite3 --symbol XAUUSD_l \
  --horizon-minutes 5 --spread-price 0.42 --slippage-price 0.10
```

Amounts are XAUUSD price differences, **not account USD or USC**. The spread
and slippage are experimental parameters, not observed execution costs.
This does not model bid/ask ticks, stops/targets, market-order latency,
commission, swap or regime shifts. Current live data may cover too short
a period for robust holdout assessment. `promotion_ready` remains false
regardless of results. No EA/MT5 order code, V2/V3 WAIT policy, safety gates,
or manual order endpoints are modified by this research experiment.

Next qualification requires multiple calendar-separated sessions, stable
out-of-sample evidence, event/news segmentation and realistic tick execution
replay with drawdown and position sizing. Do not activate a real-money scalp
lane based on this report alone.
