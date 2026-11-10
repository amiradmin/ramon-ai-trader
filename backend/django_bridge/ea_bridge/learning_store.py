"""Durable Django adapter for the existing SignalFlow learning contract."""
from datetime import datetime, timezone
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Q

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
    tags=dict(row.tags or {})
    if isinstance(tags.get("score"),(int,float)):
        tags["score"]=round(tags["score"],4)
    return {
        "trade_id": row.trade_id,
        "realized_pnl": float(row.realized_pnl),
        "opened_at": _as_epoch(row.opened_at),
        "closed_at": _as_epoch(row.closed_at),
        "risk_amount": float(row.risk_amount) if row.risk_amount is not None else None,
        "r_multiple": float(row.r_multiple) if row.r_multiple is not None else None,
        "tags": tags,
        "execution_metrics": dict(row.execution_metrics or {}),
    }


class DjangoOutcomeStore:
    """OutcomeStore backed by immutable, unique trade IDs in Django's database."""

    def add(self, record):
        # Fast-path existing trade IDs so ordinary retries/conflicts do not
        # generate expected PostgreSQL unique-constraint errors in the logs.
        # The unique constraint and IntegrityError handler below still protect
        # against concurrent inserts racing after this read.
        existing=LearningTradeOutcome.objects.filter(trade_id=record["trade_id"]).first()
        if existing is not None:
            # Server-derived context is not part of the EA payload. Apply the
            # same enrichment to retries before comparing immutable outcomes.
            for key in ("regime","setup_type","signal_id","score","strategy_profile","model_version","forecast_confirmed"):
                if key not in record["tags"] and key in existing.tags:
                    value=existing.tags[key]
                    record["tags"][key]=round(value,4) if key=="score" and isinstance(value,(int,float)) else value
            return False

        tags = dict(record.get("tags") or {})
        decision_id = str(tags.get("decision_id") or "")[:128]
        snapshot = {}
        if decision_id:
            decision = StrategyDecisionRecord.objects.filter(decision_id=decision_id).first()
            from .models import EACommand
            linked = EACommand.objects.select_related("evaluation").filter(
                command_id=tags.get("command_id", ""), decision_id=decision_id,
                instance_id=tags.get("instance_id", ""), account_login=tags.get("account_login", ""),
                symbol=tags.get("symbol", ""), evaluation__isnull=False,
            ).first()
            if linked and record["trade_id"].startswith(
                f"{linked.account_login}:{linked.payload.get('server_name', '')}:"
            ):
                decision = linked.evaluation
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
                        tags[key] = round(value,4) if key=="score" and isinstance(value,(int,float)) else value
        scalp_command = None
        if tags.get("strategy_profile") == "SCALPER_M1_M5":
            from .models import EACommand, ScalpObservation
            scalp_command = EACommand.objects.filter(command_id=tags.get('command_id',''),
                instance_id=tags.get('instance_id',''),account_login=tags.get('account_login',''),
                symbol=tags.get('symbol',''),action='OPEN',state='EXECUTED',payload__engine='SCALPER').first()
            if scalp_command and record['trade_id'].startswith(
                f"{scalp_command.account_login}:{scalp_command.payload.get('server_name','')}:"
            ):
                observation = ScalpObservation.objects.filter(pk=scalp_command.payload.get('scalp_observation_id'),
                    source=scalp_command.instance_id,account_login=scalp_command.account_login,
                    server_name=scalp_command.payload.get('server_name'),symbol=scalp_command.symbol).first()
                snapshot = dict(engine='SCALPER',observation_id=observation.pk if observation else None,
                    command_id=scalp_command.command_id,features=observation.snapshot if observation else {},
                    linkage='EXACT_COMMAND_AND_ACCOUNT_SERVER')
            else:
                scalp_command = None
        record["tags"]=dict(tags)
        try:
            with transaction.atomic():
                outcome_row = LearningTradeOutcome.objects.create(
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
                if scalp_command:
                    from .scalper_audit import append_event
                    append_event(source=scalp_command.instance_id,account_login=scalp_command.account_login,
                        server_name=scalp_command.payload['server_name'],symbol=scalp_command.symbol,
                        kind='BROKER_OUTCOME',reason='CLOSED',identity=outcome_row.trade_id,
                        observation=observation,command=scalp_command,
                        details={'trade_id':outcome_row.trade_id,'closed_at':outcome_row.closed_at.isoformat(),
                            'net_pnl':str(outcome_row.realized_pnl),'execution_metrics':outcome_row.execution_metrics})
            return True
        except IntegrityError:
            return False

    def get(self, trade_id):
        row = LearningTradeOutcome.objects.filter(trade_id=trade_id).first()
        return _record(row) if row else None

    def all(self):
        return [_record(row) for row in LearningTradeOutcome.objects.filter(
            Q(tags__strategy_profile__isnull=True) | (~Q(tags__strategy_profile="SCALPER_M1_M5") & ~Q(tags__strategy_profile__startswith="SETUP_"))).iterator()]
