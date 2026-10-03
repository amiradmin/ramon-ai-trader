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
- first bar: 2004-06-11 07:15
- last bar: 2026-01-30 23:45
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
