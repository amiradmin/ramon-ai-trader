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
    validation = data.get("validation", {})
    if validation.get("allow_lookahead") is not False:
        raise ValueError("look-ahead must remain disabled")
    return data
