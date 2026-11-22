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
