# All-Bars / Entry-Timing Benchmark

This research-only benchmark asks a different question from the matched-entry
Validation Lab:

> Does Ramon concentrate Chronos forecasts on better M15 bars, or would the same
> Chronos direction work similarly across all observed signal bars?

## Scope

The benchmark uses every unique `signal_bar_time` for which Ramon persisted a
usable Chronos decision snapshot. It chooses the **first persisted snapshot per
signal bar** so Ramon's 30-second request cadence does not overweight the same
M15 bar.

A signal bar is marked `selected` when any decision snapshot on that same bar
is linked to a real executed trade in `trade_outcomes`. This is therefore a
**bar-level entry-timing test**, not an exact reconstruction of the later
30-second snapshot that finally opened the trade.

Chronos direction is reconstructed from the stored base BUY/SELL edges. Replay
uses the same stored SL/TP geometry and Ask-aware SELL semantics as the main
Validation Lab.

## Reported groups

- `all`: every usable Chronos signal bar.
- `selected`: bars where Ramon eventually opened a real trade.
- `nonselected`: bars where Ramon did not open a real trade.

The report shows direction accuracy against the N-bar future close, mean signed
future move in ATR units, replay win rate, mean-R and PF. It also bootstraps the
selected-minus-nonselected difference in mean-R and signed future move.

A positive uplift with a 95% interval that stays above zero is evidence that
Ramon's timing/filtering is concentrating Chronos on better bars. If the interval
crosses zero, the current sample is inconclusive.

## Run

```bash
docker compose --profile tools run --rm tools \
  -m ramon.entry_timing_lab \
  --db /data/ramon_history.sqlite3 \
  --symbol XAUUSD_l \
  --horizon 4 \
  --max-bars 24 \
  --holdout 0.30 \
  --bootstrap-iterations 4000
```

This module has no live effect and must not be used to change entry thresholds
without separate out-of-sample evidence.
