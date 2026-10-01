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
  --holdout 0.30
```

Interpretation:

- The useful question is not whether Ramon is profitable on the full history.
- The useful question is whether Ramon remains better than simple/no-skill
  baselines on the final chronological holdout.
- This benchmark is intentionally simple and should not be treated as a complete
  reproduction of live TP-stage exits, slippage, or broker tick ordering.
- A later validation stage should add purged walk-forward folds, embargo,
  parameter-selection accounting, and overfitting diagnostics.
