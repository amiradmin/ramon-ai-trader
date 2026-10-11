# Research: executed-trade-first M5/M15/H1 dataset

The decision-first dataset retains the earliest snapshot per M15 signal bar;
therefore it can lose a later snapshot that actually led to a trade. This
standalone research tool instead starts with each realized `trade_outcomes`
row and performs an **exact** `(symbol,sample_key)` join to its persisted
decision snapshot. It never falls back to a nearest-time guess or changes
an unmatched trade into a labeled observation.

The three timeframes are M5/M15 from read-only SQLite plus constructed H1
from the research CSV. At decision UTC, it requires two consecutive **closed**
candles from each; no interpolation or future prices. The original broker
signal-bar timestamp is also preserved, with a separately normalized UTC
timestamp. The `broker-utc-offset-seconds` argument is required: only use
10800 for periods where the broker's UTC+3 clock has been verified.

Run:

```bash
cd ~/Documents/Presentation/ramon-ai-trader
env -u PYTHONPATH PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  uv run --no-sync pytest -q tests/test_executed_trade_dataset_builder.py

uv run --no-sync python -m ramon.executed_trade_dataset_builder \
  --db data/ramon_history.sqlite3 \
  --h1-csv data/research/constructed_h1.csv \
  --symbol XAUUSD_l \
  --broker-utc-offset-seconds 10800 \
  --output data/research/executed_trade_multitimeframe.csv \
  --report data/research/executed_trade_multitimeframe_quality.json

cat data/research/executed_trade_multitimeframe_quality.json
```

The quality report distinguishes unmatched, ambiguous, noncausal, and
missing-timeframe records, rather than silently dropping them. Exact
realized R and account-unit P/L are outcomes; they are **not features**.

Important limitations:
- Historical trade outcomes were produced by the deployed policy, and do not
  establish challenger returns or the usefulness of multi-timeframe inputs.
- Changing versions, overlapping trades, manual exits, and incomplete broker
  provenance must be examined before learning or scoring.
- Open/close timestamps, broker offset, symbol and direction are checked; a
  correct join requires the decision to precede the actual open.
- Non-`LEARNABLE` training status is kept visible; it is not quietly
  converted to training-ready outcome data.
- Evaluate any future challenger with chronological purged walk-forward splits
  and costs; keep the EA, risk rules and decision service untouched.
