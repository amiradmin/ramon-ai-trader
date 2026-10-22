"""Strategy interface and decision validation; no order submission here."""
from dataclasses import dataclass
from typing import Any, Mapping, Protocol
from .schemas import PipelineError

@dataclass(frozen=True, slots=True)
class StrategyDecision:
    decision_id: str
    symbol: str
    action: str
    confidence: float
    rationale_code: str
    model_version: str
    features: Mapping[str, Any]

class StrategyEngine(Protocol):
    def decide(self, market_snapshot: Mapping[str, Any]) -> StrategyDecision: ...

def validate_decision(d: StrategyDecision) -> StrategyDecision:
    if d.action not in {"BUY", "SELL", "HOLD"}:
        raise PipelineError("action must be BUY, SELL, or HOLD")
    if not 0 <= d.confidence <= 1:
        raise PipelineError("confidence must be in [0, 1]")
    if not d.decision_id or not d.symbol or not d.model_version:
        raise PipelineError("decision ID, symbol and model version are required")
    return d
