"""Stage 2: decide whether the opportunity is timely enough to enter."""
from typing import Any, Mapping


class EntryTimer:
    def __init__(self, minimum_quality: float = 0.65, maximum_spread_points: float = 50.0) -> None:
        if not 0.0 <= minimum_quality <= 1.0:
            raise ValueError("minimum_quality must be between 0 and 1")
        if maximum_spread_points <= 0:
            raise ValueError("maximum_spread_points must be positive")
        self.minimum_quality = minimum_quality
        self.maximum_spread_points = maximum_spread_points

    def evaluate(self, opportunity: Mapping[str, Any], market: Mapping[str, Any]) -> dict[str, Any]:
        if not opportunity.get("approved"):
            return {"approved": False, "reason": "NO_APPROVED_OPPORTUNITY"}
        try:
            quality = float(market.get("entry_quality", 0.0))
            spread = float(market.get("spread_points", float("inf")))
        except (TypeError, ValueError):
            return {"approved": False, "reason": "INVALID_ENTRY_METRICS"}
        if quality < self.minimum_quality or quality > 1.0:
            return {"approved": False, "reason": "ENTRY_QUALITY_TOO_LOW", "quality": quality, "spread_points": spread}
        if spread < 0 or spread > self.maximum_spread_points:
            return {"approved": False, "reason": "SPREAD_OUT_OF_RANGE", "quality": quality, "spread_points": spread}
        return {"approved": True, "reason": "ENTRY_TIMING_APPROVED", "quality": quality, "spread_points": spread}
