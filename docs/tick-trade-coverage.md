# Ramon V2 — tick/trade coverage gate

Use this before building any TP-before-SL training labels.

```bash
env -u PYTHONPATH PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  uv run --no-sync pytest -q tests/test_tick_trade_coverage.py

uv run --no-sync python -m ramon.tick_trade_coverage \
  --db data/ramon_history.sqlite3 \
  --ticks data/research/validated_bid_ask_ticks.csv \
  --symbol XAUUSD_l \
  --output data/research/tick_trade_coverage.json
```

The output gives counts of trades entirely inside the observed tick window, exact
decision link availability, entry fill telemetry, target-outcome coverage and
original decision stop/target distance coverage. It does not export private
trade details. The database is opened in SQLite mode=ro; it is not modified.

**Do not** interpret target_outcomes.tp1/tp2/tp3 as actual originally placed broker
SL/TP. Do not compute replay win rates until complete historical entry plans
(including original executable stop and target) are recovered.

The observed 2026-10-01 tick export corresponds to only one trading day.
Even if coverage is excellent, one day is not enough to validate a probability
model or determine trade profitability.

No live policies or risk parameters are changed.
