"""Readable orchestration for SignalFlow's four trading stages.

Stage order: opportunities -> entry timing -> risk management, with learning observing
outcomes alongside. This module evaluates a snapshot only. It does not submit orders or
persist outcomes; ``execution_enabled`` is always False.

Each stage receives the previous stages' results where that helps (entry timing sees the
opportunity, risk management sees the opportunity and the timing verdict) but no stage
reaches back to change an earlier decision.
"""
import inspect
from typing import Any, Mapping

from .entry_timing import EntryTimer
from .learning import Learning
from .opportunities import OpportunityDetector
from .risk_management import RiskManager

CONTRACT_VERSION = "2"
_STAGE_ORDER = ("opportunities", "entry_timing", "risk_management")


class SignalFlowPipeline:
    def __init__(self, opportunities: OpportunityDetector | None = None, entry_timer: EntryTimer | None = None, risk_manager: RiskManager | None = None, learning: Learning | None = None) -> None:
        self.opportunities = opportunities or OpportunityDetector()
        self.entry_timer = entry_timer or EntryTimer()
        self.risk_manager = risk_manager or RiskManager()
        self.learning = learning or Learning()

    def _evaluate_risk(self, market: Mapping[str, Any], upstream: bool, opportunity: Mapping[str, Any], timing: Mapping[str, Any]) -> dict[str, Any]:
        """Call the risk stage with stage context, tolerating custom managers with the old signature."""
        parameters = inspect.signature(self.risk_manager.evaluate).parameters
        accepts_context = "opportunity" in parameters or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values())
        if accepts_context:
            return self.risk_manager.evaluate(market, upstream, opportunity=opportunity, entry=timing)
        return self.risk_manager.evaluate(market, upstream)

    def run(self, market: Mapping[str, Any], outcome: Mapping[str, Any] | None = None) -> dict[str, Any]:
        learning_result = self.learning.observe(outcome)
        opportunity = self.opportunities.evaluate(market)
        timing = self.entry_timer.evaluate(opportunity, market)
        risk = self._evaluate_risk(market, bool(opportunity.get("approved") and timing.get("approved")), opportunity, timing)
        stages = {"opportunities": opportunity, "entry_timing": timing, "risk_management": risk}
        approved = all(bool(stages[name].get("approved")) for name in _STAGE_ORDER)
        action = opportunity.get("side", "HOLD") if approved else "HOLD"
        rejected_by = next((name for name in _STAGE_ORDER if not stages[name].get("approved")), None)
        return {
            "system": "SignalFlow",
            "contract_version": CONTRACT_VERSION,
            "action": action,
            "approved": approved,
            "rejected_by": rejected_by,
            "reason": "SIGNALFLOW_APPROVED" if approved else stages[rejected_by].get("reason", "STAGE_REJECTED"),
            "stages": {**stages, "learning": learning_result},
            "execution_enabled": False,
        }
