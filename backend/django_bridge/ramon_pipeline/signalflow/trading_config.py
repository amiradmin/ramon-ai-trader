"""Load and validate the version-controlled trading configuration."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_CONFIG_PATH = Path(__file__).with_name("trading_config.json")
_SETUP_NAMES = {
    "trend_pullback", "breakout_retest", "failed_breakout_sweep",
    "range_edge_fade", "session_opening_range_breakout",
    "volatility_compression_expansion", "exhaustion_reversal",
}


def load_trading_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load config; malformed or incomplete settings fail closed with ValueError."""
    config_path = Path(path) if path is not None else _CONFIG_PATH
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load trading config {config_path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("config_version") != "1.0.0":
        raise ValueError("trading config must be an object with config_version 1.0.0")
    finder = data.get("opportunity_finder")
    if not isinstance(finder, dict):
        raise ValueError("opportunity_finder must be an object")
    parameters = finder.get("parameters")
    if not isinstance(parameters, dict):
        raise ValueError("opportunity_finder.parameters must be an object")
    setups = finder.get("setups")
    if not isinstance(setups, dict) or set(setups) != _SETUP_NAMES:
        raise ValueError("opportunity_finder.setups must define every supported setup exactly once")
    for name, settings in setups.items():
        if not isinstance(settings, dict) or not isinstance(settings.get("enabled"), bool):
            raise ValueError(f"setup {name} must define a boolean enabled flag")
    score = finder.get("minimum_score")
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 1:
        raise ValueError("opportunity_finder.minimum_score must be between 0 and 1")
    # Validate supplemental detector parameters before any market evaluation.
    def positive_number(settings: dict[str, Any], key: str, *, allow_zero: bool = False) -> None:
        value = settings.get(key)
        valid = isinstance(value, (int, float)) and not isinstance(value, bool)
        if not valid or (value < 0 if allow_zero else value <= 0):
            comparator = "non-negative" if allow_zero else "positive"
            raise ValueError(f"setup parameter {key} must be a {comparator} number")

    sweep = setups["failed_breakout_sweep"]
    if int(sweep.get("lookback_bars", 0)) < 5:
        raise ValueError("failed_breakout_sweep.lookback_bars must be at least 5")
    positive_number(sweep, "sweep_buffer_atr", allow_zero=True)

    fade = setups["range_edge_fade"]
    if int(fade.get("lookback_bars", 0)) < 5:
        raise ValueError("range_edge_fade.lookback_bars must be at least 5")
    positive_number(fade, "edge_buffer_atr", allow_zero=True)
    wick_ratio = fade.get("minimum_rejection_wick_ratio")
    if not isinstance(wick_ratio, (int, float)) or isinstance(wick_ratio, bool) or not 0 <= wick_ratio <= 1:
        raise ValueError("range_edge_fade.minimum_rejection_wick_ratio must be between 0 and 1")

    session = setups["session_opening_range_breakout"]
    if int(session.get("opening_range_minutes", 0)) < 15:
        raise ValueError("session_opening_range_breakout.opening_range_minutes must be at least 15")
    positive_number(session, "breakout_buffer_atr", allow_zero=True)
    sessions = session.get("sessions")
    if not isinstance(sessions, dict) or not sessions:
        raise ValueError("session_opening_range_breakout.sessions must be a non-empty object")
    for label, entry in sessions.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("timezone"), str) or not isinstance(entry.get("open"), str):
            raise ValueError(f"session {label} must define timezone and open")
        from datetime import time
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            ZoneInfo(entry["timezone"])
            time.fromisoformat(entry["open"])
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError(f"session {label} has an invalid timezone or open time") from exc

    compression = setups["volatility_compression_expansion"]
    if int(compression.get("compression_lookback_bars", 0)) < 2:
        raise ValueError("volatility_compression_expansion.compression_lookback_bars must be at least 2")
    positive_number(compression, "compression_max_atr")
    positive_number(compression, "expansion_body_atr")

    exhaustion = setups["exhaustion_reversal"]
    if int(exhaustion.get("extension_lookback_bars", 0)) < 5:
        raise ValueError("exhaustion_reversal.extension_lookback_bars must be at least 5")
    positive_number(exhaustion, "minimum_extension_atr")
    extreme = exhaustion.get("rsi_extreme")
    if not isinstance(extreme, (int, float)) or isinstance(extreme, bool) or not 50 < extreme <= 100:
        raise ValueError("exhaustion_reversal.rsi_extreme must be in (50, 100]")
    if exhaustion.get("require_choch") is not True:
        raise ValueError("exhaustion_reversal.require_choch must remain true")

    validation = data.get("validation", {})
    if validation.get("allow_lookahead") is not False:
        raise ValueError("look-ahead must remain disabled")
    return data
