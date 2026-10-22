"""Persisted decision orchestration with an independent, fail-closed risk gate.

This module intentionally does not deliver intents to MT5. It is a safe
persistence boundary until a real strategy adapter and EA command protocol pass
demo-account integration tests.
"""
from __future__ import annotations

from decimal import Decimal
from hashlib import sha256
from typing import Any, Mapping

from django.db import transaction

from ea_bridge.models import OrderIntentRecord, RiskVerdictRecord, StrategyDecisionRecord
from .execution import build_intent
from .risk import RiskPolicy, RiskVerdict, enforce_fail_closed
from .strategy import StrategyDecision, StrategyEngine, validate_decision


def _intent_id_for(decision_id: str) -> str:
    # Deterministic IDs make retries of the same decision idempotent.
    return "ramon-" + sha256(decision_id.encode("utf-8")).hexdigest()[:48]


@transaction.atomic
def evaluate_and_record(
    market_snapshot: Mapping[str, Any],
    account_snapshot: Mapping[str, Any],
    *,
    strategy_engine: StrategyEngine,
    risk_policy: RiskPolicy | None,
    instance_id: str,
) -> dict[str, Any]:
    """Evaluate one snapshot and persist decision/risk/intent atomically.

    Exceptions or missing risk policy never authorize an order. Callers should
    persist operational errors separately; this function never calls MT5.
    """
    if not instance_id or len(instance_id) > 128:
        raise ValueError("valid instance_id is required")
    decision = validate_decision(strategy_engine.decide(market_snapshot))
    StrategyDecisionRecord.objects.get_or_create(
        decision_id=decision.decision_id,
        defaults={
            "symbol": decision.symbol,
            "action": decision.action,
            "confidence": decision.confidence,
            "rationale_code": decision.rationale_code,
            "model_version": decision.model_version,
            "features": dict(decision.features),
        },
    )

    if decision.action == "HOLD":
        verdict = RiskVerdict(False, "NO_TRADE_ACTION")
    else:
        verdict = enforce_fail_closed(risk_policy, decision, account_snapshot, market_snapshot)

    RiskVerdictRecord.objects.get_or_create(
        decision_id=decision.decision_id,
        reason_code=verdict.reason_code,
        defaults={
            "approved": verdict.approved,
            "approved_volume": Decimal(str(verdict.approved_volume)),
            "stop_loss": Decimal(str(verdict.stop_loss)) if verdict.stop_loss is not None else None,
            "take_profit": Decimal(str(verdict.take_profit)) if verdict.take_profit is not None else None,
            "details": {"approved": verdict.approved, "reason_code": verdict.reason_code},
        },
    )

    intent = build_intent(decision, verdict, _intent_id_for(decision.decision_id))
    persisted_intent = None
    if intent is not None:
        persisted_intent, _ = OrderIntentRecord.objects.get_or_create(
            intent_id=intent.intent_id,
            defaults={
                "decision_id": intent.decision_id,
                "instance_id": instance_id,
                "symbol": intent.symbol,
                "side": intent.side,
                "volume": Decimal(str(intent.volume)),
                "stop_loss": Decimal(str(intent.stop_loss)),
                "take_profit": Decimal(str(intent.take_profit)) if intent.take_profit is not None else None,
                "state": OrderIntentRecord.State.PENDING,
            },
        )

    return {
        "decision_id": decision.decision_id,
        "action": decision.action,
        "risk_approved": verdict.approved,
        "risk_reason": verdict.reason_code,
        "intent_id": persisted_intent.intent_id if persisted_intent else None,
        "intent_state": persisted_intent.state if persisted_intent else None,
        "mt5_delivery_enabled": False,
    }
