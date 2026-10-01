# Ramon Validation Lab

This lab is a research-only benchmark. It does not change live trading.

It replays each stored Ramon decision sample with the same entry spread and the
same stored stop/target distances, then compares:

- Ramon recorded BUY/SELL direction
- deterministic random BUY/SELL
- always BUY
- always SELL
- previous completed M15 candle direction

The last 30% of samples are reported separately as a chronological holdout.
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
