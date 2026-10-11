# Offline trade rejection diagnostics

Explain *why* the executed-trade-first M5/M15/H1 exporter excludes trades.
No change to EA, risk, execution or SQLite; all database access is read-only.
The audit uses the same exact symbol/sample_key join, UTC conversion, causal
checks and first-missing-timeframe rule as the executed-trade builder.

Run after installing/pulling a passing version:

```bash
cd ~/Documents/Presentation/ramon-ai-trader
uv run --no-sync python -m ramon.trade_rejection_audit \
  --db data/ramon_history.sqlite3 \
  --h1-csv data/research/constructed_h1.csv \
  --symbol XAUUSD_l \
  --broker-utc-offset-seconds 10800 \
  --output data/research/trade_rejection_details.csv \
  --report data/research/trade_rejection_quality.json
```

The console summarizes first rejection reasons and the report JSON also
contains exact missing candle UTC opens. The CSV has one row per trade with
causal time delta (`opened - captured`), directions, timestamps, primary
reason and all detected causal violations. Summary `first_rejection_counts`
must sum to `trades_scanned`.

The `missing_candle_open_timestamps` fields identify exact gaps to
investigate with an independent MT5 history export. A gap is **not**
permission to synthesize a candle. If an existing OHLC row fails validation,
it is flagged as context invalid without fabricating a nonexistent timestamp.

Check broker UTC+3 provenance independently for the audited historical
period. A wrong offset can cause misleading rejection classifications.
Trade closure time and net R are outcomes only, never input features.
For chronology, preserve the originally captured UTC; never move the
decision timestamp forward to make an invalid trade eligible.
