# Ramon Historical Lab — external XAUUSD M15 data

This path is intentionally isolated from Ramon's live symbol and EA behavior.

## Why

The Kaggle XAUUSD archive can be used to expand historical M15 coverage for
research, regime analysis and walk-forward validation. It must not be treated as
LiteFinance execution history because the source broker, spread and candle source
can differ.

External bars are therefore imported under the default synthetic symbol
`XAUUSD_KAGGLE` and `spread_points=0` means **unknown external spread**, not a
zero-spread trade.

## Audit the uploaded archive

```bash
uv run python -m ramon.kaggle_history /path/to/archive.zip --audit-only
```

Expected archive member for Ramon is `XAU_15m_data.csv`.

The archive timestamps are treated as source wall-clock time. This dataset defaults
to `UTC+3`, and the importer converts them to canonical UTC epoch seconds before
storage. Override only when auditing a source with a different documented offset:

```bash
uv run python -m ramon.kaggle_history /path/to/archive.zip \
  --audit-only --source-utc-offset-hours 3
```

The audit reports:

- invalid OHLC/date rows
- duplicate timestamps
- non-monotonic rows
- M15 timestamp alignment
- zero-volume rows
- regular 15-minute intervals
- larger gaps and the largest observed gap

A gap is reported, not automatically filled. Weekend/session gaps must not be
fabricated into synthetic candles.

## Import into a separate research database

Recommended:

```bash
uv run python -m ramon.kaggle_history /path/to/archive.zip \
  --db /data/ramon_kaggle_m15.sqlite3 \
  --symbol XAUUSD_KAGGLE
```

The importer audits before writing and refuses structurally dirty data by
default. `--allow-dirty` exists only for controlled investigation.

Do not import this dataset as `XAUUSD_l`. Keeping the external source under a
separate symbol prevents accidental mixing with LiteFinance live/execution data.

## Verified dataset snapshot used during implementation

The supplied archive was checked locally before this importer was committed:

- timeframe: M15
- rows: 494,235
- valid rows: 494,235
- duplicate timestamps: 0
- non-monotonic rows: 0
- misaligned timestamps: 0
- zero-volume rows: 0
- source timezone used: UTC+3
- first bar after UTC normalization: 2004-06-11 04:15Z
- last bar after UTC normalization: 2026-01-30 20:45Z
- regular 15-minute intervals: 485,714
- larger gaps: 8,520
- largest gap: 2,793,600 seconds

These figures describe this archive only; re-run the audit whenever the source
file changes.

## Next research step

Use this database for Ramon-only M15 experiments and walk-forward comparisons.
Do not change the live EA or production decision thresholds based solely on
in-sample results from this external broker dataset. Final validation should
still include held-out LiteFinance data and realistic execution assumptions.


## Historical benchmark lab

The external M15 database can be evaluated without changing the live EA:

```bash
PYTHONPATH=src python3 -m ramon.historical_benchmark \
  --db data/ramon_kaggle_m15.sqlite3 \
  --symbol XAUUSD_KAGGLE \
  --folds 5 \
  --stride 4 \
  --fallback-spread 42 \
  --output data/ramon_historical_benchmark.json
```

The first benchmark intentionally compares only two simple, non-trained reference
forecasters:

- `previous_bar`: projects the most recent completed M15 bar move.
- `momentum_4bar`: projects the mean of the last four completed-bar changes.

The first 20% of bars are excluded from scored results and remain available only
as historical context. The remaining 80% is reported both as one continuous
evaluation and as chronological, non-overlapping folds.

Each model report includes:

- trades, BUY and SELL counts
- wins, losses and timeouts
- resolved win rate
- timeout rate
- gross positive/negative R
- profit factor
- mean R and net R
- max drawdown in R
- BUY/SELL metrics separately
- calendar-year metrics
- fold-by-fold metrics

These baseline models are not fitted, so the folds are chronological evaluation
windows rather than train/test fits. When a trainable model is added, it must be
trained only on observations strictly earlier than the evaluated fold.

For this external dataset, `spread_points=0` means unknown. The benchmark therefore
requires an explicit fallback spread (42 points in the example). This is a
research assumption, not a claim about historical Octa or LiteFinance spreads.

The benchmark remains deliberately separate from RANGE execution, quote-level
micro context, slippage and live EA exit management. A favorable result here is
not sufficient evidence for a live configuration change.
