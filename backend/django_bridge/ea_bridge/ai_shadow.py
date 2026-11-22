"""Read-only ML shadow scoring for independent M15 strategy workers.

A model score is advisory and must never participate in command creation,
risk approval, worker readiness, or order execution.
"""
from __future__ import annotations

import hashlib
import io
import math

from django.core.files.storage import default_storage

from .models import LearningModelRun


def _features(decision, setup):
    if decision.get("decision") not in ("BUY", "SELL"):
        return None
    stages = decision.get("stages") or {}
    opportunity = stages.get("opportunities") or {}
    timing = stages.get("entry_timing") or {}
    setup_details = opportunity.get("setup") or {}
    values = {
        "tag_side": decision["decision"],
        "tag_strategy_profile": str(setup),
        "tag_setup_type": str(setup_details.get("type") or setup),
    }
    regime = opportunity.get("regime")
    if isinstance(regime, str) and regime:
        values["tag_regime"] = regime
    for name, value in (
        ("tag_score", opportunity.get("score")),
        ("snapshot_confidence", opportunity.get("score")),
        ("snapshot_atr", setup_details.get("atr")),
        ("snapshot_spread_points", timing.get("spread_points")),
    ):
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)):
            values[name] = float(value)
    return values


def observe_shadow(decision, *, symbol, setup):
    """Return a serializable score or a clear non-prediction status.

    The score is expected R-multiple, not calibrated win probability.
    Only a validated, immutable, checksum-matched local training artifact is
    eligible. Errors remain within the observer and cannot block trading.
    """
    result = {"mode": "SHADOW_ONLY", "model": "trade_performance", "status": "UNAVAILABLE"}
    try:
        features = _features(decision, setup)
        if features is None:
            return {**result, "status": "NO_CANDIDATE"}
        run = (LearningModelRun.objects.select_related("job")
               .filter(model_key="trade_performance", job__symbol=symbol,
                       status=LearningModelRun.Status.PASSED,
                       validation__passed=True)
               .exclude(artifact_path="").order_by("-training_finished_at", "-id").first())
        if run is None:
            return {**result, "status": "NO_VALIDATED_MODEL"}
        if run.artifact_format != "joblib" or not run.artifact_sha256 or not run.artifact_size_bytes:
            return {**result, "status": "ARTIFACT_INVALID"}
        # A bounded read prevents unexpectedly large or replaced artifacts.
        if run.artifact_size_bytes > 20_000_000:
            return {**result, "status": "ARTIFACT_INVALID"}
        with default_storage.open(run.artifact_path, "rb") as handle:
            raw = handle.read(run.artifact_size_bytes + 1)
        if (len(raw) != run.artifact_size_bytes or
                hashlib.sha256(raw).hexdigest() != run.artifact_sha256):
            return {**result, "status": "ARTIFACT_MISMATCH"}
        # Joblib is pickle-based: only internally generated, checksum-pinned
        # training artifacts must be admitted. Never load external uploads.
        import joblib
        bundle = joblib.load(io.BytesIO(raw))
        if bundle.get("model_key") != "trade_performance" or bundle.get("target") != "r_multiple":
            return {**result, "status": "MODEL_CONTRACT_MISMATCH"}
        vectorizer = bundle["vectorizer"]
        trained_features = set(bundle.get("feature_names") or ())
        overlap = trained_features.intersection(features)
        if len(overlap) < 2 or "tag_side" not in overlap:
            return {**result, "status": "FEATURE_COVERAGE_INSUFFICIENT"}
        predicted = float(bundle["estimator"].predict(vectorizer.transform([features]))[0])
        if not math.isfinite(predicted):
            return {**result, "status": "PREDICTION_INVALID"}
        return {**result, "status": "SCORED", "expected_r": round(predicted, 4),
                "model_version": run.version, "matched_features": len(overlap),
                "trained_features": len(trained_features),
                "note": "Research estimate only; no live order or risk influence."}
    except Exception:
        # Fail isolated: do not expose artifact paths, account data or traceback.
        return {**result, "status": "OBSERVER_ERROR"}


def observe_replay_shadow(decision, *, symbol, setup, market_bars, spread_points):
    """Isolated research replay score. Never a broker-validated probability."""
    status = {"mode": "SHADOW_ONLY", "training_source": "SIMULATED_REPLAY",
              "model": "replay_expected_r", "status": "UNAVAILABLE"}
    if decision.get("decision") not in ("BUY", "SELL"):
        return {**status, "status": "NO_CANDIDATE"}
    try:
        from .replay_ml import make_features
        if not market_bars:
            return {**status, "status": "NO_MARKET_BARS"}
        signal_at = int(market_bars[-1]["time"])
        features = make_features(market_bars, signal_at=signal_at, setup=setup,
                                 side=decision["decision"], spread_points=spread_points)
        run = (LearningModelRun.objects.select_related("job")
               .filter(model_key="replay_expected_r", job__symbol=symbol,
                       status=LearningModelRun.Status.PASSED, validation__passed=True)
               .exclude(artifact_path="").order_by("-training_finished_at", "-id").first())
        if run is None:
            return {**status, "status": "NO_VALIDATED_REPLAY_MODEL"}
        if (run.artifact_format != "joblib" or not run.artifact_sha256 or
                not run.artifact_size_bytes or run.artifact_size_bytes > 20_000_000):
            return {**status, "status": "ARTIFACT_INVALID"}
        with default_storage.open(run.artifact_path, "rb") as handle:
            raw = handle.read(run.artifact_size_bytes + 1)
        if len(raw) != run.artifact_size_bytes or hashlib.sha256(raw).hexdigest() != run.artifact_sha256:
            return {**status, "status": "ARTIFACT_MISMATCH"}
        import joblib
        bundle = joblib.load(io.BytesIO(raw))
        if bundle.get("model_key") != "replay_expected_r" or bundle.get("target") != "r_multiple":
            return {**status, "status": "MODEL_CONTRACT_MISMATCH"}
        trained = set(bundle.get("feature_names") or ())
        # All numeric features must be present for a meaningful replay score.
        if not {name.split("=", 1)[0] for name in trained}.issubset(features):
            return {**status, "status": "FEATURE_COVERAGE_INSUFFICIENT"}
        prediction = float(bundle["estimator"].predict(bundle["vectorizer"].transform([features]))[0])
        if not math.isfinite(prediction):
            return {**status, "status": "PREDICTION_INVALID"}
        return {**status, "status": "SCORED_REPLAY", "expected_r": round(prediction, 4),
                "model_version": run.version,
                "note": "Simulated fixed-spread replay estimate; not a live-trade win probability."}
    except Exception:
        return {**status, "status": "OBSERVER_ERROR"}


def observe_replay_classifiers(decision, *, symbol, setup, market_bars, spread_points):
    """Observe replay success and future-regime estimators without affecting execution."""
    from .replay_ml import make_features
    base = {"mode": "SHADOW_ONLY", "training_source": "SIMULATED_REPLAY"}
    output = {}
    specifications = {
        "replay_trade_success": ("target_before_stop", "tp_before_sl_fraction"),
        "replay_market_regime": ("forward_4bar_regime", "regime_probabilities"),
    }
    try:
        if not market_bars:
            return {key: {**base, "status": "NO_MARKET_BARS"} for key in specifications}
        ts = int(market_bars[-1]["time"])
        action = decision.get("decision")
        for key, (target, field) in specifications.items():
            if key == "replay_trade_success" and action not in ("BUY", "SELL"):
                output[key] = {**base, "status": "NO_CANDIDATE"}
                continue
            features = make_features(
                market_bars, signal_at=ts,
                setup=setup if key == "replay_trade_success" else "MARKET",
                side=action if key == "replay_trade_success" else "BUY",
                spread_points=spread_points if key == "replay_trade_success" else 0)
            if key == "replay_market_regime":
                features = {k: v for k, v in features.items() if not k.startswith("tag_")}
            try:
                run = (LearningModelRun.objects.select_related("job")
                       .filter(model_key=key, job__symbol=symbol,
                               status=LearningModelRun.Status.PASSED, validation__passed=True)
                       .exclude(artifact_path="").order_by("-training_finished_at", "-id").first())
                if run is None:
                    output[key] = {**base, "status": "NO_VALIDATED_MODEL"}
                    continue
                if (run.artifact_format != "joblib" or not run.artifact_sha256
                        or not run.artifact_size_bytes or run.artifact_size_bytes > 20_000_000):
                    output[key] = {**base, "status": "ARTIFACT_INVALID"}
                    continue
                with default_storage.open(run.artifact_path, "rb") as handle:
                    raw = handle.read(run.artifact_size_bytes + 1)
                if len(raw) != run.artifact_size_bytes or hashlib.sha256(raw).hexdigest() != run.artifact_sha256:
                    output[key] = {**base, "status": "ARTIFACT_MISMATCH"}
                    continue
                import joblib
                bundle = joblib.load(io.BytesIO(raw))
                if bundle.get("model_key") != key or bundle.get("target") != target:
                    output[key] = {**base, "status": "MODEL_CONTRACT_MISMATCH"}
                    continue
                trained = {name.split("=", 1)[0] for name in bundle.get("feature_names", ())}
                if not trained.issubset(features):
                    output[key] = {**base, "status": "FEATURE_COVERAGE_INSUFFICIENT"}
                    continue
                probabilities = bundle["estimator"].predict_proba(
                    bundle["vectorizer"].transform([features]))[0]
                classes = list(bundle["estimator"].classes_)
                if any(not math.isfinite(float(p)) for p in probabilities):
                    output[key] = {**base, "status": "PREDICTION_INVALID"}
                    continue
                if key == "replay_trade_success":
                    if 1 not in classes:
                        output[key] = {**base, "status": "MODEL_CONTRACT_MISMATCH"}
                        continue
                    values = {field: round(float(probabilities[classes.index(1)]), 4)}
                else:
                    values = {field: {str(cls): round(float(p), 4)
                             for cls, p in zip(classes, probabilities)}}
                output[key] = {**base, **values, "status": "SCORED_REPLAY",
                    "model_version": run.version,
                    "note": "Simulation-based uncalibrated research output; not live-trade probability."}
            except Exception:
                output[key] = {**base, "status": "OBSERVER_ERROR"}
        return output
    except Exception:
        return {key: {**base, "status": "OBSERVER_ERROR"} for key in specifications}
