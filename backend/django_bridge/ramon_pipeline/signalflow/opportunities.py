"""Stage 1: identify whether the market snapshot contains an opportunity."""
from typing import Any, Mapping


class OpportunityDetector:
    def __init__(self, minimum_score: float = 0.65) -> None:
        if not 0.0 <= minimum_score <= 1.0:
            raise ValueError("minimum_score must be between 0 and 1")
        self.minimum_score = minimum_score

    def evaluate(self, market: Mapping[str, Any]) -> dict[str, Any]:
        side = str(market.get("trend", "")).upper()
        try:
            score = float(market.get("setup_score", 0.0))
        except (TypeError, ValueError):
            score = 0.0
        if side not in {"BUY", "SELL"}:
            return {"approved": False, "side": "HOLD", "score": score, "reason": "NO_VALID_DIRECTION"}
        if not 0.0 <= score <= 1.0:
            return {"approved": False, "side": "HOLD", "score": score, "reason": "INVALID_SETUP_SCORE"}
        if score < self.minimum_score:
            return {"approved": False, "side": "HOLD", "score": score, "reason": "SETUP_SCORE_TOO_LOW"}
        return {"approved": True, "side": side, "score": score, "reason": "OPPORTUNITY_FOUND"}
