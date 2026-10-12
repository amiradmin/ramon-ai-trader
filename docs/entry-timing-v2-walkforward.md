# Entry Timing v2 — multi-fold walk-forward (observe only)

This expands the single 70/30 split with four **chronological expanding-train / forward-test** windows, a five-minute embargo, and no overlapping synthetic positions within each strategy/test window. Only completed M1/M5 candles are inputs.

Compare `momentum`, `mean_reversion`, `reversal`, and `mtf_agreement`. Each fold selects its candidate strictly from its training portion; its test data never affects selection. Every candidate's test performance is reported as an **audit**, not as permission to trade.

```bash
cd ~/Documents/Presentation/ramon-ai-trader
git pull --ff-only origin main
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -q tests/test_entry_timing_v2_walkforward.py
PYTHONPATH=src uv run python -m ramon.entry_timing_v2_walkforward \
  --db data/ramon_history.sqlite3 --symbol XAUUSD_l \
  --folds 4 --horizon-minutes 5 --train-fraction 0.5 \
  --point-size 0.01 --spread-price 0.42 --slippage-price 0.10
```

Check the actual symbol point size with MT5 before trusting spread conversion: the price spread approximation is `spread_points * point_size`; `--spread-price` is used only if the recorded spread is unavailable/zero. Set `--fixed-spread` to force the assumed `--spread-price` for scenario comparison. The spread stored in historical exports might not represent contemporaneous executable bid/ask and should not be mistaken for observed fill cost.

The fold output includes win rate, average net **price** movement, total net **price** movement, max peak-to-trough drawdown in price-distance units, and per-UTC-hour aggregates. These are not account profit/loss or capital drawdown. No ticket fills, commissions, slippage distribution, news embargoes or actual stop/target execution are replayed. Expanding training windows overlap, and two consecutive folds are not independent statistical trials. `promotion_ready` is always false.

The current ~1.5k M1 bars cover a short period; collect multiple independent trading days before considering any live evaluation. This module does not change the EA, model decision, dashboard GO, or manual/automatic execution permissions.
