"""Fail-closed bridge from an approved advisory risk verdict to one EA command.

This module is only called when both command-creation and command-execution gates
are explicitly enabled. It revalidates fresh account telemetry and never sizes a
trade itself; the persisted risk verdict must already contain complete values.
"""
import hashlib
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from ramon_pipeline.signalflow.risk_management import RiskConfig

from .models import AccountDailyBaseline, EACommand, EAHeartbeat, RiskVerdictRecord, StrategyDecisionRecord


def _is_demo_account(snapshot):
    """Only an explicit integer trade-mode value from broker telemetry counts as demo."""
    return (
        isinstance(snapshot, dict)
        and type(snapshot.get("account_trade_mode")) is int
        and snapshot.get("account_trade_mode") == 0
    )


def _decimal(value, field):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{field} is not numeric") from None
    if not number.is_finite():
        raise ValueError(f"{field} is not finite")
    return number


def create_open_command(*, decision, verdict, instance_id, account_login, server_name):
    """Return (command, created, reason); never create anything unless both gates are on."""
    if not getattr(settings, "RAMON_EA_COMMAND_CREATION_ENABLED", False):
        return None, False, "COMMAND_CREATION_DISABLED"
    if not getattr(settings, "RAMON_EA_COMMAND_EXECUTION_ENABLED", False):
        return None, False, "COMMAND_EXECUTION_DISABLED"
    if not isinstance(decision, StrategyDecisionRecord) or decision.action not in ("BUY", "SELL"):
        return None, False, "DIRECTIONAL_DECISION_REQUIRED"
    if not isinstance(verdict, RiskVerdictRecord) or not verdict.approved:
        return None, False, "RISK_VERDICT_NOT_APPROVED"
    if verdict.decision_id != decision.decision_id:
        return None, False, "DECISION_VERDICT_MISMATCH"
    if not instance_id or not account_login or not server_name:
        return None, False, "ACCOUNT_IDENTITY_MISSING"

    now = timezone.now()
    heartbeat = EAHeartbeat.objects.filter(instance_id=instance_id).first()
    if heartbeat is None:
        return None, False, "ACCOUNT_TELEMETRY_UNAVAILABLE"
    age = (now - heartbeat.last_seen).total_seconds()
    if age < 0 or age > 90:
        return None, False, "ACCOUNT_TELEMETRY_STALE"
    if heartbeat.account_login != account_login:
        return None, False, "ACCOUNT_LOGIN_MISMATCH"
    if heartbeat.server_name != server_name:
        return None, False, "ACCOUNT_SERVER_MISMATCH"
    if heartbeat.symbol != decision.symbol:
        return None, False, "ACCOUNT_SYMBOL_MISMATCH"
    if heartbeat.status != "TELEMETRY_OK" or not heartbeat.terminal_connected:
        return None, False, "ACCOUNT_TELEMETRY_NOT_READY"
    if not heartbeat.trade_allowed:
        return None, False, "TERMINAL_TRADE_PERMISSION_DENIED"

    snapshot = heartbeat.payload if isinstance(heartbeat.payload, dict) else {}
    positions = snapshot.get("positions")
    account = snapshot.get("account")
    quote = snapshot.get("quote")
    broker_day_raw = snapshot.get("broker_day")
    if not isinstance(positions, list) or positions:
        return None, False, "OPEN_POSITION_RISK_UNVERIFIED"
    if not isinstance(account, dict) or not isinstance(quote, dict):
        return None, False, "ACCOUNT_SNAPSHOT_INVALID"
    quote_time = parse_datetime(str(snapshot.get("quote_time", "")))
    if quote_time is None or quote_time.tzinfo is None or quote_time.utcoffset() is None:
        return None, False, "QUOTE_TIMESTAMP_INVALID"
    quote_age = (now - quote_time).total_seconds()
    if quote_age < -5 or quote_age > 60:
        return None, False, "QUOTE_STALE"
    if not isinstance(broker_day_raw, str):
        return None, False, "DAILY_LOSS_BASELINE_UNAVAILABLE"

    try:
        from datetime import date
        broker_day = date.fromisoformat(broker_day_raw)
        if broker_day.isoformat() != broker_day_raw:
            raise ValueError("invalid broker day")
        bid = _decimal(quote.get("bid"), "bid")
        ask = _decimal(quote.get("ask"), "ask")
        current_balance = _decimal(account.get("balance"), "balance")
        baseline_equity = _decimal(account.get("equity"), "equity")
        decision_account = decision.features.get("account_snapshot", {})
        decision_balance = _decimal(decision_account.get("balance"), "decision balance")
        decision_equity = _decimal(decision_account.get("equity"), "decision equity")
        baseline = AccountDailyBaseline.objects.filter(
            account_login=account_login,
            server_name=server_name,
            broker_day=broker_day,
        ).first()
        volume = _decimal(verdict.approved_volume, "approved_volume")
        stop_loss = _decimal(verdict.stop_loss, "stop_loss")
        take_profit = _decimal(verdict.take_profit, "take_profit")
    except (ValueError, TypeError, InvalidOperation):
        return None, False, "COMMAND_RISK_VALUES_INVALID"

    if baseline is None or baseline.opening_balance <= 0:
        return None, False, "DAILY_LOSS_BASELINE_UNAVAILABLE"
    if verdict.details.get("daily_baseline_day") != broker_day.isoformat():
        return None, False, "RISK_VERDICT_BASELINE_DAY_MISMATCH"
    if bid <= 0 or ask < bid or current_balance <= 0 or baseline_equity <= 0 or volume <= 0:
        return None, False, "ACCOUNT_OR_QUOTE_VALUES_INVALID"
    if abs(current_balance - decision_balance) > Decimal("0.01"):
        return None, False, "ACCOUNT_BALANCE_CHANGED_AFTER_DECISION"
    if abs(baseline_equity - decision_equity) > Decimal("0.01"):
        return None, False, "ACCOUNT_EQUITY_CHANGED_AFTER_DECISION"

    daily_loss_percent = max(
        Decimal("0"),
        (baseline.opening_balance - baseline_equity) / baseline.opening_balance * Decimal("100"),
    )
    if daily_loss_percent >= Decimal(str(RiskConfig().max_daily_loss_percent)):
        return None, False, "DAILY_LOSS_LIMIT_REACHED"

    if decision.action == "BUY":
        risk_distance, reward_distance = ask - stop_loss, take_profit - ask
    else:
        risk_distance, reward_distance = stop_loss - bid, bid - take_profit
    if risk_distance <= 0 or reward_distance <= 0:
        return None, False, "INVALID_STOP_OR_TARGET"
    reward_risk = reward_distance / risk_distance
    if reward_risk < Decimal(str(RiskConfig().min_reward_risk)):
        return None, False, "REWARD_RISK_TOO_LOW"

    # One deterministic command per decision/account/EA prevents repeated requests
    # for the same M15 bar from generating duplicate broker orders.
    command_id = "ramon-open-" + hashlib.sha256(
        f"{decision.decision_id}|{instance_id}|{account_login}".encode()
    ).hexdigest()[:32]
    payload = {
        "side": decision.action,
        "volume": float(volume),
        "stop_loss": float(stop_loss),
        "take_profit": float(take_profit),
    }
    with transaction.atomic():
        command, created = EACommand.objects.get_or_create(
            command_id=command_id,
            defaults={
                "instance_id": instance_id,
                "account_login": account_login,
                "symbol": decision.symbol,
                "action": EACommand.Action.OPEN,
                "payload": payload,
                "decision_id": decision.decision_id,
                "risk_verdict_id": str(verdict.pk),
                "execution_authorized": True,
                "expires_at": now + timedelta(seconds=60),
            },
        )
    if (
        command.instance_id != instance_id
        or command.account_login != account_login
        or command.symbol != decision.symbol
        or command.decision_id != decision.decision_id
    ):
        return None, False, "COMMAND_ID_COLLISION"
    return command, created, "COMMAND_CREATED" if created else "COMMAND_ALREADY_EXISTS"
