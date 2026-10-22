"""Durable order-intent and EA acknowledgement contracts."""
from dataclasses import dataclass
from enum import StrEnum
from .risk import RiskVerdict
from .strategy import StrategyDecision

class IntentState(StrEnum):
    PENDING = "PENDING"
    DELIVERED = "DELIVERED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"

@dataclass(frozen=True, slots=True)
class OrderIntent:
    intent_id: str
    decision_id: str
    symbol: str
    side: str
    volume: float
    stop_loss: float
    take_profit: float | None
    state: IntentState = IntentState.PENDING

def build_intent(decision: StrategyDecision, verdict: RiskVerdict, intent_id: str) -> OrderIntent | None:
    if decision.action == "HOLD" or not verdict.approved:
        return None
    if decision.action not in {"BUY", "SELL"} or verdict.stop_loss is None or verdict.approved_volume <= 0:
        return None
    return OrderIntent(intent_id, decision.decision_id, decision.symbol, decision.action, verdict.approved_volume, verdict.stop_loss, verdict.take_profit)
