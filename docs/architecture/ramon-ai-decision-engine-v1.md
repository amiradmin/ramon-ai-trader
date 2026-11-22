# Ramon AI Decision Engine v1 — shadow observer

This initial milestone is **read-only and shadow-only**. The seven M15 strategy containers continue to use their existing deterministic SignalFlow opportunity, entry timing, risk and EA command path. The new `ea_bridge.ai_shadow.observe_shadow` runs **after** `evaluate_payload` returns, which is after the legacy command creation path. Its output is written only into `StrategyWorkerControl.latest.ai_shadow` and never used by execution authorization or worker readiness.

## What it does

For a BUY/SELL candidate, the observer looks for the latest `PASSED`, `validation.passed=true`, internally trained `trade_performance` Random Forest model belonging to the **exact worker symbol**. It verifies the artifact size and SHA256 before loading it. Only pre-decision features are supplied. A minimum feature overlap is required. The model returns a **predicted R-multiple** (`expected_r`), not a win probability, and is labeled advisory. Failure states include `NO_CANDIDATE`, `NO_VALIDATED_MODEL`, `ARTIFACT_INVALID`, `ARTIFACT_MISMATCH`, `MODEL_CONTRACT_MISMATCH`, `FEATURE_COVERAGE_INSUFFICIENT`, `PREDICTION_INVALID`, and `OBSERVER_ERROR`. These states are not trade vetoes.

This version does **not** train a new classifier, estimate calibrated TP-before-SL probabilities, prioritize trades across workers, or adapt trading thresholds. A `PASSED` research regression holdout is not sufficient to authorize autonomous trading.

## Deployment / validation

After reviewing and pulling `main`, run:

```bash
docker compose exec django python manage.py test ea_bridge.tests_ai_shadow
docker compose exec django python manage.py check
docker compose up -d --build django trend-pullback breakout-retest failed-breakout-sweep range-edge-fade session-opening-range-breakout volatility-compression-expansion exhaustion-reversal
```

Inspect an M15 strategy's `latest.ai_shadow` in its control data. If no validated model for the exact `XAUUSD_l` symbol exists, `NO_VALIDATED_MODEL` is expected. Training remains user-triggered through the existing Learning Engine. Training artifacts are local joblib/pickle files and must never be supplied by untrusted users. No trading flags, account scope, EA deployment, settings or Docker volumes need to change.

## Gates before any ML influence

1. Fix broker M15 timestamp alignment and validate historical source quality.
2. Collect sufficient *independent* closed trade labels, with consistent pre-trade features for each strategy and symbol.
3. Validate chronological walk-forward results out of sample, including transaction costs, calibration if introducing probabilities, and a deterministic baseline.
4. Compare shadow scores against actual future trade outcomes, monitor concept drift and missing data, and require explicit manual approval before any risk-neutral veto or ranking experiment.
5. Retain deterministic risk caps and fail-closed execution in all future variants.

A shadow prediction is not evidence of profitability or broker parity.

## Training from the stored 2026-09-22 to 2026-11-22 XAUUSD_l backtest

The backtest export's trade ledger describes **simulated stop/target fills**, not confirmed broker orders. Replay results live in the shared `historical-replay-jobs` media directory, while the underlying M15 OHLC candles live in the `MarketBar` PostgreSQL table. The research trainer reconstructs 60-candle features strictly up to each signal-time candle and verifies the replay's exact candle fingerprint against the current database. This prevents using post-trade prices as predictors.

Run on the local checkout after pulling main:

```bash
docker compose exec django python manage.py test ea_bridge.tests_replay_ml ea_bridge.tests_ai_shadow
docker compose exec django python manage.py train_replay_shadow --symbol XAUUSD_l
```

The trainer automatically chooses the newest **completed** saved job with that symbol. For an exact saved job use `--job-id <32-character-job-id>`. It requires at least 48 closed simulated trades, at least 20 distinct signal timestamps, at least 40 training trades and 8 held-out trades, a matching OHLC fingerprint, and at most 5% rejected/off-grid M15 rows. A chronological time-grouped holdout must beat the training median baseline on both MAE and RMSE. On rejection, the tool reports the reason and does **not** generate a tradable model. Weekend gaps are not filled with invented candles.

A passing model is recorded as `replay_expected_r`, *separate* from the live-trade outcome `trade_performance` model. The M15 worker's `latest.ai_replay_shadow` displays its simulated expected-R estimate; `latest.ai_shadow` continues to represent the broker-trade model. **Neither score changes orders, signal selection, risk budgets, permissions, or EA configuration.** No model estimates calibrated win probabilities in this milestone.

If the exported JSON is only on the laptop rather than stored as a completed replay job, rerun the simulator using its original source, range and assumptions first. Simply having candles in PostgreSQL does not mean the prior trade-result file is present for training.

## Phase 1: three distinct models (November 2026)

The `train_replay_shadow` command now attempts **three separately validated models**, each stored under its own `LearningModelRun.model_key`:

| Model key | Source label | Output | Validation baseline |
| --- | --- | --- | --- |
| `replay_market_regime` | Next four continuous M15 closes relative to current ATR | Probabilities for UP / DOWN / RANGE | Constant class-prior multiclass log loss |
| `replay_trade_success` | Simulated TARGET before STOP, using only completed replay trades | Uncalibrated TP-before-SL fraction | Constant prior Brier score |
| `replay_expected_r` | Stop-to-entry-normalized simulated trade outcome | Expected R-multiple | Median R MAE and RMSE |

These are three **research candidate models**, not production AI trading models. The future four-bar regime classification is a *forward direction label*, not interchangeable with an existing contemporaneous ADX/structure regime. Trade success is conditional on the replay's fixed spread, fill assumptions and trade-selection policy and is **not** a calibrated live-broker win probability. Samples failing the input fingerprint or market-data quality guard are rejected. Missing trade labels reject only the relevant trade models, not the forward-regime learner.

The independent M15 worker snapshot includes `latest.ai_market_regime`, `latest.ai_trade_success`, and `latest.ai_replay_shadow` (expected R). Older `latest.ai_shadow` continues to show the distinct broker-trade research model, if available. These fields are written only **after** the command path and are never used to create or veto orders.

After pulling, run:

```bash
docker compose exec django python manage.py test \
  ea_bridge.tests_replay_ml ea_bridge.tests_replay_three_models ea_bridge.tests_ai_shadow

docker compose exec django python manage.py train_replay_shadow --symbol XAUUSD_l
```

Inspect the output for PASSED versus REJECTED **per model**. A failed research model must remain unavailable in the shadow observer. The models require a completed simulator job in the existing shared media volume and exact history fingerprint; a backtest JSON imported only as candles in PostgreSQL is not automatically a completed simulator job.

Before eventual live influence, require stronger walk-forward validation over multiple market regimes and dates, a genuine broker execution dataset, appropriate probability calibration, comparison against deterministic strategy baselines, and explicit operational review.
