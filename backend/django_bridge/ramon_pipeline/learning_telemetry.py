"""Outcome attribution separate from live order processing."""
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping
from .schemas import utc_now

@dataclass(frozen=True, slots=True)
class DecisionOutcome:
    decision_id: str
    event_id: str
    symbol: str
    action: str
    outcome: str
    realized_pnl: float | None
    captured_at: datetime
    context: Mapping[str, Any]

def outcome_from_event(*, decision_id: str, event_id: str, symbol: str, action: str, outcome: str, realized_pnl: float | None, context: Mapping[str, Any]) -> DecisionOutcome:
    return DecisionOutcome(decision_id, event_id, symbol, action, outcome, realized_pnl, utc_now(), dict(context))
