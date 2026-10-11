# Offline M5/M15/H1 context with constructed H1 CSV

The H1 research export has been verified locally by the operator:
2,735 complete hourly bars from 10,950 M15 bars; five incomplete
hour periods were rejected. The native H1 SQLite timeframe remains empty.

This adapter reads M5/M15 from the main history SQLite database **read-only**
and H1 from the separate verified research CSV, then fails closed if any
of the last two completed bars for any timeframe are absent or stale.

```bash
cd ~/Documents/Presentation/ramon-ai-trader
env -u PYTHONPATH PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  uv run --no-sync pytest -q tests/test_multitimeframe_h1_overlay.py

uv run --no-sync python -m ramon.multitimeframe_h1_overlay \
  --db data/ramon_history.sqlite3 \
  --h1-csv data/research/constructed_h1.csv \
  --symbol XAUUSD_l \
  --decision-utc 1788811200 \
  --output data/research/context_with_h1.json

python3 - <<'PY'
import json
r=json.load(open('data/research/context_with_h1.json'))
print('Ready:',r['ready'])
for tf,x in r['timeframes'].items():
    print(tf,x['ready'],x['reason'],len(x['bars']))
PY
```

**Important:** the example decision timestamp is an illustration and may be
near one of the five missing hour buckets; `ready=false` is a valid,
informative result. Use an actual historical decision UTC from the period
where your M5/M15 rows overlap and H1 has been constructed.

This is a point-in-time DATA COVERAGE check, not an AI prediction or
performance benchmark. Do not add these inferred H1 values to the production
SQLite table or enable the live EA to use them. The next milestone is a
time-aligned, out-of-sample M15-only versus multi-timeframe comparison.
