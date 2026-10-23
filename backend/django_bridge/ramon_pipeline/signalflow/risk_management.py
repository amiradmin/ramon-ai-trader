"""Stage 3: size a trade within a conservative configured risk budget."""
import math
from typing import Any, Mapping


class RiskManager:
    def __init__(self, maximum_risk_percent: float = 0.5) -> None:
        if not 0.0 < maximum_risk_percent <= 100.0:
            raise ValueError("maximum_risk_percent must be in (0, 100]")
        self.maximum_risk_percent = maximum_risk_percent

    def evaluate(self, market: Mapping[str, Any], upstream_approved: bool) -> dict[str, Any]:
        if not upstream_approved:
            return {"approved": False, "reason": "UPSTREAM_STAGE_REJECTED", "volume": 0.0}
        required = ("account_balance", "risk_percent", "stop_loss_points", "point_value_per_lot", "volume_step", "min_volume", "max_volume")
        try:
            values = {key: float(market[key]) for key in required}
        except (KeyError, TypeError, ValueError):
            return {"approved": False, "reason": "MISSING_OR_INVALID_RISK_INPUT", "volume": 0.0}
        balance, requested_pct, stop_points, point_value, step, min_volume, max_volume = (values[k] for k in required)
        if not all(math.isfinite(v) for v in values.values()):
            return {"approved": False, "reason": "NON_FINITE_RISK_INPUT", "volume": 0.0}
        if balance <= 0 or requested_pct <= 0 or stop_points <= 0 or point_value <= 0 or step <= 0 or min_volume <= 0 or max_volume < min_volume:
            return {"approved": False, "reason": "NON_POSITIVE_OR_INCONSISTENT_RISK_INPUT", "volume": 0.0}
        risk_pct = min(requested_pct, self.maximum_risk_percent)
        risk_budget = balance * risk_pct / 100.0
        raw_volume = risk_budget / (stop_points * point_value)
        volume = math.floor((min(raw_volume, max_volume) + 1e-12) / step) * step
        volume = round(volume, 8)
        if volume < min_volume:
            return {"approved": False, "reason": "RISK_BUDGET_BELOW_MINIMUM_VOLUME", "volume": 0.0, "risk_budget": risk_budget}
        estimated_risk = volume * stop_points * point_value
        if estimated_risk > risk_budget + 1e-9:
            return {"approved": False, "reason": "RISK_BUDGET_EXCEEDED", "volume": 0.0, "risk_budget": risk_budget}
        return {"approved": True, "reason": "RISK_APPROVED", "volume": volume, "risk_budget": round(risk_budget, 8), "estimated_risk": round(estimated_risk, 8), "risk_percent": risk_pct}
