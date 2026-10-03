# Cost-aware BUY / SELL / WAIT research

`ramon.trade_selector_lab` is an offline experiment. Neither the installed EA nor the dashboard needs an update for it. It has no live server import, order route, automatic promotion or shadow writer. Dashboard integration is a separate step after a model survives external and broker validation.

## Protocol

The advisor learns two action values: hypothetical BUY net R and SELL net R. It selects the higher predicted value only above a training-selected threshold; otherwise it chooses WAIT. This is expected-return regression, not a calibrated probability classifier.

Labels use the existing historical simulator's assumptions: next-bar open entry; BUY pays Ask and SELL closes on Ask; stop 1.5 signal ATR, target 3 signal ATR; four-bar maximum holding; stop-first if both levels touch; timeout closes on the final Bid/Ask. Subtract an additional 0.1R round-trip cost. Positive recorded spread is used; missing/zero spread uses 42 points at point 0.01. Eight execution parity tests cover BUY and SELL with stop, target, both-hit and timeout exits.

Features use the existing 22 local technical/SMC values, with price levels anchored to completed signal close/ATR, plus signal spread/ATR and ATR/close. Those two cost/volatility features use only information available at the signal. Means/scales are fitted on training rows only. No remote artifact, pickle or joblib is loaded.

Five expanding chronological outer folds score the last 80% of the archive. For each fold:

1. Gather past-only labels every 16 bars. All label endpoints must be strictly before the outer evaluation start.
2. Reserve the last 25% of that past history for inner chronological validation. Purge training labels reaching the inner validation start.
3. Train BUY/SELL ridge models with penalties 0.1, 1 and 10. Test predicted net-R entry thresholds 0, 0.05 and 0.1 in inner validation only. A candidate needs at least 30 non-overlapping validation trades, PF > 1 and MeanR > 0; rank qualifying candidates by MeanR.
4. If none qualifies, the outer policy is WAIT for the whole fold. Do not use the held-out outer results to choose an alternative.
5. If a candidate qualifies, refit on all matured past labels and freeze it for the outer fold. Evaluate every four bars, with one non-overlapping position at a time.

Timestamp deltas must be exactly 900 seconds throughout each signal-to-horizon interval. Context may contain prior session gaps. This future-timestamp check defines offline eligible samples; it cannot be deployed as an entry filter.

The same eligible grids, prices, exits and costs are used for always-BUY, always-SELL, previous-bar/momentum direction and their contrarian policies. These reference policies do **not** use the old forecast-strength entry filters; their reports therefore differ from the earlier forecast lab. Flat baseline price moves yield WAIT.

## Outputs and gate

The report preserves every inner candidate's metrics, selected parameters or rejection, training/validation boundaries, five-fold and pooled PF/MeanR/net R/closed-trade drawdown, data fingerprint and implementation file hashes. All fitted inner pairs are saved locally as JSON, including rejected candidates. Selected outer refits are also saved when applicable. `report.partial.json` is intermediate; `report.json` contains the completed experiment.

The outer gate requires at least 30 trades, PF > 1 and MeanR > 0 in every fold, plus MeanR better than every reference policy in every fold. Passing only permits the next independent broker validation step. It never enables Shadow or live trading. Drawdown still requires review in that next step; closed-trade R is not account equity drawdown.

The archive was already used in prior research iterations. These folds are algorithmically out of sample for each training/selection operation, but they are not a pristine holdout for the whole research project. A fresh broker confirmation remains necessary. M15 bars cannot reconstruct tick timing, slippage or installed EA position management.

## Run

```bash
git pull --ff-only
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest \
  tests/test_trade_selector_lab.py tests/test_smc_local_lab.py \
  tests/test_historical_benchmark.py tests/test_research_shadow.py tests/test_progress.py -q
.venv/bin/python -m ramon.trade_selector_lab \
  --db data/ramon_kaggle_m15.sqlite3 --symbol XAUUSD_KAGGLE \
  --output-dir data/trade_selector_lab
```

The run uses one Python CPU process and shows terminal progress. All experiment settings above are fixed in this initial protocol. Results and models under `data/` are not committed; a report snapshot and analysis are provided under `docs/` after evaluation.
