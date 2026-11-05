"""Durable Django adapter for the existing SignalFlow learning contract."""
from datetime import datetime, timezone
from decimal import Decimal

from django.db import IntegrityError, transaction

from ea_bridge.models import LearningTradeOutcome, StrategyDecisionRecord


def _as_datetime(value):
    if value is None:
        return None
    return datetime.fromtimestamp(float(value), tz=timezone.utc)


def _as_epoch(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def _record(row):
    return {
        "trade_id": row.trade_id,
        "realized_pnl": float(row.realized_pnl),
        "opened_at": _as_epoch(row.opened_at),
        "closed_at": _as_epoch(row.closed_at),
        "risk_amount": float(row.risk_amount) if row.risk_amount is not None else None,
        "r_multiple": float(row.r_multiple) if row.r_multiple is not None else None,
        "tags": dict(row.tags or {}),
        "execution_metrics": dict(row.execution_metrics or {}),
    }


class DjangoOutcomeStore:
    """OutcomeStore backed by immutable, unique trade IDs in Django's database."""

    def add(self, record):
        # Fast-path existing trade IDs so ordinary retries/conflicts do not
        # generate expected PostgreSQL unique-constraint errors in the logs.
        # The unique constraint and IntegrityError handler below still protect
        # against concurrent inserts racing after this read.
        if LearningTradeOutcome.objects.filter(trade_id=record["trade_id"]).exists():
            return False

        tags = dict(record.get("tags") or {})
        decision_id = str(tags.get("decision_id") or "")[:128]
        snapshot = {}
        if decision_id:
            decision = StrategyDecisionRecord.objects.filter(decision_id=decision_id).first()
            if decision:
                snapshot = {
                    "decision_id": decision.decision_id,
                    "symbol": decision.symbol,
                    "action": decision.action,
                    "confidence": decision.confidence,
                    "rationale_code": decision.rationale_code,
                    "model_version": decision.model_version,
                    "features": decision.features if isinstance(decision.features, dict) else {},
                    "created_at": decision.created_at.isoformat() if decision.created_at else None,
                }
                features = snapshot["features"]
                stages = features.get("stages") if isinstance(features.get("stages"), dict) else {}
                opportunity = stages.get("opportunities") if isinstance(stages.get("opportunities"), dict) else {}
                regime = opportunity.get("regime") if isinstance(opportunity.get("regime"), dict) else {}
                setup = opportunity.get("setup") if isinstance(opportunity.get("setup"), dict) else {}
                derived_tags = {
                    "regime": regime.get("label"),
                    "setup_type": setup.get("type"),
                    "signal_id": opportunity.get("signal_id"),
                    "score": opportunity.get("score"),
                    "strategy_profile": features.get("strategy_profile"),
                    "model_version": snapshot.get("model_version"),
                    "forecast_confirmed": opportunity.get("reason") == "OPPORTUNITY_FOUND_WITH_FORECAST_CONFIRMATION",
                }
                for key, value in derived_tags.items():
                    if value is not None and key not in tags:
                        tags[key] = value
        try:
            with transaction.atomic():
                LearningTradeOutcome.objects.create(
                    trade_id=record["trade_id"],
                    decision_id=decision_id,
                    instance_id=str(tags.get("instance_id") or "")[:128],
                    account_login=str(tags.get("account_login") or "")[:32],
                    symbol=str(tags.get("symbol") or snapshot.get("symbol") or "")[:32],
                    realized_pnl=Decimal(str(record["realized_pnl"])),
                    risk_amount=Decimal(str(record["risk_amount"])) if record.get("risk_amount") is not None else None,
                    r_multiple=Decimal(str(record["r_multiple"])) if record.get("r_multiple") is not None else None,
                    opened_at=_as_datetime(record.get("opened_at")),
                    closed_at=_as_datetime(record.get("closed_at")),
                    tags=tags,
                    execution_metrics=dict(record.get("execution_metrics") or {}),
                    decision_snapshot=snapshot,
                )
            return True
        except IntegrityError:
            return False

    def get(self, trade_id):
        row = LearningTradeOutcome.objects.filter(trade_id=trade_id).first()
        return _record(row) if row else None

    def all(self):
        return [_record(row) for row in LearningTradeOutcome.objects.all().iterator()]
