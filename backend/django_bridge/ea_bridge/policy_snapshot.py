"""Deterministic, read-only SignalFlow policy snapshots for replay/runtime parity."""
import hashlib
import json

from .decision import _build_pipeline


def effective_profiles():
    profiles = {}
    for name, demo_mode in (("standard", False), ("active_demo", True)):
        pipeline = _build_pipeline(demo_mode=demo_mode)
        risk = pipeline.risk_manager
        profiles[name] = {
            "detector_demo_mode": bool(demo_mode),
            "compare_candidates": bool(pipeline.compare_candidates),
            "maximum_risk_percent": float(risk.maximum_risk_percent),
            "strict_structural": bool(risk.config.strict_structural),
            "short_stop_spread_adjust": bool(risk.config.short_stop_spread_adjust),
            "stop_buffer_atr": float(risk.config.stop_buffer_atr),
            "min_stop_atr": float(risk.config.min_stop_atr),
            "max_stop_atr": float(risk.config.max_stop_atr),
            "maximum_spread_points": float(pipeline.entry_timer.maximum_spread_points),
            "minimum_entry_quality": float(pipeline.entry_timer.minimum_quality),
            "minimum_opportunity_score": float(pipeline.opportunities.minimum_score),
        }
    return profiles


def fingerprint(profiles):
    serialized = json.dumps(profiles, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
