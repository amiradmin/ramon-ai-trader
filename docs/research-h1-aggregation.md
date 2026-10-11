# Research-only H1 constructed from M15

On the HP-Mini, the reported XAUUSD_l M15 database has 10,950 rows in
2,740 UTC hour buckets: 2,735 buckets have four timestamps exactly at
HH:00, HH:15, HH:30, HH:45; five buckets are incomplete.

This tool reads SQLite using mode=ro, skips all incomplete or invalid
buckets, and writes **only** local research CSV and quality JSON.

```bash
env -u PYTHONPATH PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  uv run --no-sync pytest -q tests/test_research_h1_from_m15.py

uv run --no-sync python -m ramon.research_h1_from_m15 \
  --db data/ramon_history.sqlite3 --symbol XAUUSD_l \
  --output data/research/constructed_h1.csv \
  --report data/research/constructed_h1_quality.json

cat data/research/constructed_h1_quality.json
```

Expected **only if source data remains unchanged**: 2,735 full H1 bars
and 5 skipped periods. The test suite uses synthetic data; real counts
must be checked after running locally.

Candle aggregation: open from first M15; high=max highs; low=min lows;
close from fourth M15. Do not fill missing quarters or use a
partially formed current candle.

**Important**: This does not insert H1 into `history_bars`. The separate
`multitimeframe_context_audit` currently reads H1 from SQLite and will
still report H1 missing until an explicitly validated research-only
overlay is built. Do not enable any live multi-timeframe policy yet.
The constructed series may differ from native broker H1 (for example,
if session boundaries/time semantics differ), so compare it against
native H1 when available. No live EA or Docker changes.
