"""Independent fail-closed risk gate contract."""
from dataclasses import dataclass
from typing import Any, Mapping, Protocol
from .strategy import StrategyDecision

@dataclass(frozen=True, slots=True)
class RiskVerdict:
    approved: bool
    reason_code: str
    approved_volume: float = 0.0
    stop_loss: float | None = None
    take_profit: float | None = None

class RiskPolicy(Protocol):
    def evaluate(self, decision: StrategyDecision, account: Mapping[str, Any], market: Mapping[str, Any]) -> RiskVerdict: ...

def enforce_fail_closed(policy: RiskPolicy | None, decision: StrategyDecision, account: Mapping[str, Any], market: Mapping[str, Any]) -> RiskVerdict:
    if policy is None:
        return RiskVerdict(False, "RISK_POLICY_UNAVAILABLE")
    try:
        verdict = policy.evaluate(decision, account, market)
    except Exception:
        return RiskVerdict(False, "RISK_POLICY_ERROR")
    if not verdict.approved:
        return verdict
    if verdict.approved_volume <= 0:
        return RiskVerdict(False, "INVALID_APPROVED_VOLUME")
    if verdict.stop_loss is None:
        return RiskVerdict(False, "STOP_LOSS_REQUIRED")
    return verdict
