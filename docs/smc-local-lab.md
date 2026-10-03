# SMC_LOCAL five-fold research lab

This module is isolated from live imports, services and EA inputs. It uses the existing local SMC feature formulas; it never downloads or deserializes third-party models. The first experiment is a fixed ridge regression of four-bar future return in signal ATR units. It is a locally trained research baseline, not a reproduction of a vendor model.

On 2026-10-03, the external archive contains 494,235 M15 bars (2004–2026), 8,520 timestamp discontinuities, no recorded spreads, and 464,658 valid H4 signal-to-outcome intervals after 256-bar warmup. These overlapping intervals are not independent observations. Broker history contains 10,525 M15 bars, 10,507 known spreads, 116 discontinuities and 9,817 contiguous H4 intervals. Counts support initial research; neither sample size nor directional accuracy establishes a trading edge. Broker validation is required separately.

## Fixed protocol

- Reserve the first 20% as initial past-only training, then evaluate five chronological blocks covering the remaining 80%.
- At each block start, fit on expanding historical data only. Require every training label end to be strictly before the block start. Keep previous evaluation periods in later training only after their labels have matured.
- Train every 16 bars and evaluate every four bars. The fixed training density reduces redundant labels and computation; no parameter search is performed. Replay baselines at exactly the same fold bounds, horizon and evaluation stride.
- Extract features from at most 256 completed bars. Anchor price-level features to current close/ATR, then fit means/scales on training data only. ATR and indicator context may contain historical session gaps.
- Use ridge penalty 1.0. Forecast uncertainty is the 80th percentile absolute training residual, not a calibrated probability. No fitting on held-out outcomes or tuning after inspecting results.
- All signal-to-horizon intervals must have exactly 900-second deltas. This is offline sample eligibility based on future timestamps, not a live entry rule. Each simulated position remains inside its evaluation block.
- Apply recorded spreads if positive, otherwise 42 points, plus a fixed additional 0.1R round-trip cost. Reuse the historical simulator's stop-first M15 exits and non-overlapping trades. Report PF, MeanR, net R and closed-trade drawdown for each fold and the pooled stream.
- Preserve JSON-only frozen models with feature names, training bounds, scaling, uncertainty and data fingerprint. Report the exact settings and implementation file hashes.

## Run

No model dependencies beyond the project's Python environment are required. The computation uses one Python CPU process.

```bash
git pull --ff-only
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest tests/test_smc_local_lab.py tests/test_historical_benchmark.py tests/test_research_shadow.py tests/test_progress.py -q
.venv/bin/python -m ramon.smc_local_lab \
  --db data/ramon_kaggle_m15.sqlite3 --symbol XAUUSD_KAGGLE \
  --horizon 4 --stride 4 --train-stride 16 \
  --fallback-spread 42 --cost-r 0.1 \
  --output-dir data/smc_local_lab \
  --shadow-db data/smc_local_oos_shadow.sqlite3
```

Progress appears on stderr. Each completed fold writes `report.partial.json`; only `report.json` confirms a complete run. The final model artifacts are fold-specific, not a live deployment artifact. Reports under `data/` are ignored by Git.

## Shadow gate

Before running, the conservative gate is fixed: at least 30 trades in **each** fold, PF > 1 and MeanR > 0 in **all** folds, and MeanR better than each of the four reference baselines in **each** fold. Drawdown is reported for review; this gate does not imply an acceptable account-level risk or authorize live trading.

Only if the gate passes and `--shadow-db` is provided does the lab record historical held-out advisory votes in a **new dedicated research database**. It refuses the input database and any existing shadow output database. An unsuccessful run creates no shadow database. These are historical OOS votes; no realtime server hook, installed EA change or order is created. Confidence is left unset because the residual interval is not calibrated.

Further held-out broker testing, calibration and execution validation are required before any live change. No unknown zero-spread data is represented as free execution, and no hypothetical promotion is performed automatically.
