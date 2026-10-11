# Ramon V2 — Offline Multi-Timeframe Dataset Builder

This tool creates a **research-only** CSV from historical decision snapshots
and exact linked closed trade outcomes. It does not train a model, infer a
counterfactual P/L, modify production SQLite, or change live trading policy.

Data contract:
- For each recorded M15 signal bar select the **earliest** eligible
  `decision_samples` snapshot by `(captured, id)`. Do not select by
  whether the trade won or had a label.
- At that captured UTC timestamp require two exact, completed M5/M15
  SQLite candles and two completed **constructed H1** candles from the
  separately validated research CSV. Missing context -> exclude, not fill.
- Features are deterministic and available at decision time: latest close,
  two-bar return, last range, last candle body for each timeframe.
- Match an outcome only by **exact** `symbol + sample_key`. A single
  trade with finite `net_r` and `net_units`, and opened no earlier than
  the selected decision, may be labeled `REALIZED_TRADE`.
- No matched trade => `UNTRADED_OR_UNKNOWN` with **blank** net R, net
  units and label-end timestamp. Ambiguous or inconsistent records likewise
  get no numeric label.

Run tests, then build locally:

```bash
cd ~/Documents/Presentation/ramon-ai-trader
env -u PYTHONPATH PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  uv run --no-sync pytest -q tests/test_multitimeframe_dataset_builder.py

uv run --no-sync python -m ramon.multitimeframe_dataset_builder \
  --db data/ramon_history.sqlite3 \
  --h1-csv data/research/constructed_h1.csv \
  --symbol XAUUSD_l \
  --output data/research/multitimeframe_decisions.csv \
  --report data/research/multitimeframe_dataset_quality.json

cat data/research/multitimeframe_dataset_quality.json
```

The earlier database inventory showed 11,059 raw decisions within the
historical M5 range, 383 signal bars, and 98 trades matching a
`sample_key` in that broad range. Of these, 9,542 snapshots have
three-timeframe coverage covering 332 signal bars. **These are not
counts of ready, uniquely-labeled samples**. The builder uses the first
snapshot per bar without post-hoc selection and requires a causal matched
trade; final usable counts must come from its actual quality report.

Limitations:
- A full sample still is not an independent trade opportunity; correlated
  bars, different versions and changing regimes must be accounted for.
- One snapshot per signal bar can exclude later traded snapshots. No
  retrospective relabeling is permitted.
- `REALIZED_TRADE` denotes a realized original policy outcome, not
  what a proposed challenger would earn.
- Splits must use `label_end_utc` and embargo before fitting any
  challenger. Do not score unlabeled records as losses or wins.
- Derived H1 may differ from native broker H1; validate provenance.
- Keep local reports/CSV private and do not commit them.
