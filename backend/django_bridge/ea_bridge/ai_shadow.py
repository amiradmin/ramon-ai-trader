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
