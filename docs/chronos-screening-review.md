# Chronos screening review — 2026-10-03

Research only. No installed EA, live service, order routing, or thresholds were changed.

The completed report uses stride 128 and assumed spread 42 points. It predates the explicit gap guard and records an unknown model revision. Its results are preliminary; retain the original artifact rather than overwrite it.

| Horizon (M15 bars) | Trades | PF | MeanR | NetR | Max closed DD (R) |
|---|---:|---:|---:|---:|---:|
| 1 | 126 | 0.592 | -0.090 | -11.388 | 12.844 |
| 4 | 159 | 0.736 | -0.113 | -18.043 | 19.043 |
| 8 | 250 | 0.634 | -0.202 | -50.602 | 56.708 |
| 16 | 384 | 0.744 | -0.160 | -61.565 | 78.306 |

All aggregate horizons lose money. H4 has only two positive independent chronological folds out of five. TREND_DOWN H8 has 21 trades, PF 1.339, MeanR 0.144, but only one positive fold; H16 has 31 trades, PF 1.030, MeanR 0.016 and only one positive fold. Sparse positive buckets do not justify selecting a deployable edge or random threshold tuning.

Decision: no robust Chronos candidate established by this screening. The next research direction is a locally reproduced SMC_LOCAL model, trained strictly before each of five chronological evaluation folds. Purge training labels that overlap each evaluation start; fit scaling on training data only; save feature schema, model provenance, PF/MeanR/DD, per-fold stability and shadow votes in a separate research database. Implementation and validation of that trainable model remain future work; the existing feature extractor and shadow-vote storage are infrastructure, not a trained advisor.

## Gap guard

The historical lab now excludes any signal-to-horizon interval containing a timestamp delta other than exactly 900 seconds. This includes entry gaps, weekends, missing candles, duplicate timestamps and reversed timestamps. Both raw direction analyses and trade/fold/regime replays use the same rule before inference. Trade replays and raw direction reports count skipped windows. Historical input context may still contain gaps.

This is offline dataset eligibility based on future timestamps, not a deployable entry filter. The resulting metrics describe contiguous intervals, not performance across all sessions. Stop-loss gap fill/slippage remains unmodelled. Existing JSON artifacts are not retroactively repaired.

## Validation and next command

Run the focused checks after pulling:

```bash
git pull --ff-only
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest tests/test_progress.py tests/test_historical_benchmark.py tests/test_research_shadow.py -q
```

For a reproducible replacement screening, use a separate output and cache until a pinned Chronos revision is recorded. This command is supplied for review; it has not been launched by this review.

```bash
docker compose --profile tools run --rm --cpus=2 \
  -v "$PWD:/workspace" -w /workspace -e PYTHONPATH=/workspace/src \
  tools -m ramon.historical_benchmark \
  --db /data/ramon_kaggle_m15.sqlite3 --symbol XAUUSD_KAGGLE \
  --folds 5 --stride 128 --fallback-spread 42 --analysis-horizons 1,4,8,16 \
  --include-chronos --chronos-model autogluon/chronos-2-small \
  --chronos-device cpu --cpu-workers 2 --chronos-stride 128 \
  --chronos-cache /data/chronos_historical_gap_forecasts.sqlite3 \
  --output /data/ramon_historical_benchmark_chronos_gap.json
```
