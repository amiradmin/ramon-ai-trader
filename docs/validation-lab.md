# Ramon Validation Lab

This lab is a research-only benchmark. It does not change live trading.

It replays only real executed Ramon entries joined back to their saved decision
snapshots. It uses the same stored entry spread and stop/target distances, then compares:

- Ramon recorded BUY/SELL direction
- deterministic random BUY/SELL
- always BUY
- always SELL
- previous completed M15 candle direction

The last 30% of executed entries are reported separately as a chronological holdout.
The lab also runs expanding-window purged walk-forward folds with an embargo,
paired Ramon-minus-baseline bootstrap confidence intervals, a 1,000-seed random
direction distribution, and an ambiguous same-bar SL/TP count.
Replay starts from the first complete M15 bar after the actual fill so the entry
bar cannot leak pre-entry high/low extremes into the counterfactual result.
Because M15 OHLC cannot reveal intrabar ordering, a bar that touches both SL and
TP is counted conservatively as SL-first.

Run:

```bash
docker compose --profile tools run --rm tools \
  -m ramon.validation_lab \
  --db /data/ramon_history.sqlite3 \
  --symbol XAUUSD_l \
  --max-bars 24 \
  --holdout 0.30 \
  --folds 5 \
  --embargo-bars 4 \
  --bootstrap-iterations 4000 \
  --random-seeds 1000
```

Interpretation:

- The useful question is not whether Ramon is profitable on the full history.
- The useful question is whether Ramon remains better than simple/no-skill
  baselines on the final chronological holdout.
- This is a matched-timing direction benchmark: baselines trade only when Ramon
  actually entered. It tests direction skill, not Ramon's entry-timing skill.
- Stored Ramon SL/TP geometry is reused for every baseline, so the comparison
  intentionally isolates direction under the same exit geometry.
- The random baseline uses many deterministic seeds and reports Ramon's percentile
  within that distribution instead of relying on one random draw.
- Purged walk-forward removes development entries whose replay horizon plus
  embargo could overlap the next OOS block.
- Paired bootstrap intervals are descriptive uncertainty estimates; overlapping
  trades can still reduce effective independence.
- This benchmark is not a complete reproduction of live TP-stage exits, slippage,
  bid/ask exit-side behavior, swaps, latency, or broker tick ordering.
- Next validation work: audit bid/ask bar semantics and execution timing, add
  all-bars timing baselines and ablations, then trial logging for CPCV/PBO/DSR.


## UTC / fill / Ask-side accuracy

The validation lab now keeps two time bases deliberately separate:

- `entry_time`: raw MT5 broker clock, used only to locate the corresponding
  `CopyRates` M15 bars.
- `entry_time_utc`: canonical chronology for holdout splits, purging and
  walk-forward folds. It is computed as `opened - opened_utc_offset_seconds`;
  legacy rows without a stored offset fall back to the UTC decision receipt time.

SELL replay is Ask-aware. Stored MT5 OHLC is treated as the chart/Bid side, and
each future bar uses `Bid OHLC + spread_points * point` as the Ask proxy. When
a bar lacks spread telemetry, the matched entry spread is used as a fallback.

New closed trades can also persist the volume-weighted actual entry fill price.
That value is retained for execution/slippage audit; baseline entry geometry
still uses the same matched decision-side Bid/Ask for every strategy so the
direction comparison remains apples-to-apples.
