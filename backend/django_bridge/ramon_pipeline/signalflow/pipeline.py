"""Readable orchestration for SignalFlow's four trading stages.

This module evaluates a snapshot only. It does not submit orders or persist outcomes.
"""
from typing import Any, Mapping

from .entry_timing import EntryTimer
from .learning import Learning
from .opportunities import OpportunityDetector
from .risk_management import RiskManager


class SignalFlowPipeline:
    def __init__(self, opportunities: OpportunityDetector | None = None, entry_timer: EntryTimer | None = None, risk_manager: RiskManager | None = None, learning: Learning | None = None) -> None:
        self.opportunities = opportunities or OpportunityDetector()
        self.entry_timer = entry_timer or EntryTimer()
        self.risk_manager = risk_manager or RiskManager()
        self.learning = learning or Learning()

    def run(self, market: Mapping[str, Any], outcome: Mapping[str, Any] | None = None) -> dict[str, Any]:
        learning_result = self.learning.observe(outcome)
        opportunity = self.opportunities.evaluate(market)
        timing = self.entry_timer.evaluate(opportunity, market)
        risk = self.risk_manager.evaluate(market, bool(opportunity.get("approved") and timing.get("approved")))
        approved = bool(opportunity.get("approved") and timing.get("approved") and risk.get("approved"))
        action = opportunity.get("side", "HOLD") if approved else "HOLD"
        return {
            "system": "SignalFlow",
            "action": action,
            "approved": approved,
            "stages": {
                "opportunities": opportunity,
                "entry_timing": timing,
                "risk_management": risk,
                "learning": learning_result,
            },
            "execution_enabled": False,
        }
