"""Stage 4: validate trade outcomes for later durable learning/analysis."""
from typing import Any, Mapping


class Learning:
    def observe(self, outcome: Mapping[str, Any] | None) -> dict[str, Any]:
        if outcome is None:
            return {"accepted": False, "reason": "NO_OUTCOME_PROVIDED"}
        trade_id = str(outcome.get("trade_id", "")).strip()
        try:
            pnl = float(outcome["realized_pnl"])
        except (KeyError, TypeError, ValueError):
            return {"accepted": False, "reason": "INVALID_OUTCOME"}
        if not trade_id:
            return {"accepted": False, "reason": "MISSING_TRADE_ID"}
        if pnl > 0:
            result = "WIN"
        elif pnl < 0:
            result = "LOSS"
        else:
            result = "BREAKEVEN"
        return {"accepted": True, "trade_id": trade_id, "realized_pnl": pnl, "result": result, "reason": "OUTCOME_VALIDATED_NOT_PERSISTED"}
