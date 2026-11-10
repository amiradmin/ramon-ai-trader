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

from .models import EACommand, EAHeartbeat, RiskVerdictRecord, StrategyDecisionRecord


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


@transaction.atomic
def create_open_command(*, decision, verdict, instance_id, account_login, server_name, evaluation=None):
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
    if evaluation is not None and (
        evaluation.verdict_id != verdict.pk or evaluation.decision_id != decision.decision_id
        or evaluation.instance_id != instance_id or evaluation.account_login != account_login
        or evaluation.server_name != server_name or evaluation.action != decision.action
        or evaluation.symbol != decision.symbol
    ):
        return None, False, "EVALUATION_SCOPE_MISMATCH"
    if not instance_id or not account_login or not server_name:
        return None, False, "ACCOUNT_IDENTITY_MISSING"

    now = timezone.now()
    heartbeat = EAHeartbeat.objects.select_for_update().filter(instance_id=instance_id).first()
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
    if not _is_demo_account(snapshot):
        return None, False, "DEMO_ACCOUNT_REQUIRED"
    positions = snapshot.get("positions")
    account = snapshot.get("account")
    quote = snapshot.get("quote")
    from .scalper_engine import allow_main_alongside_scalps
    from .strategy_registry import STRATEGIES, verified_engine_positions, strategy_commands
    strategy_setup = decision.features.get('strategy_setup')
    if strategy_setup:
        from .models import StrategyWorkerControl
        if strategy_setup not in STRATEGIES:
            return None, False, 'UNKNOWN_STRATEGY'
        control = StrategyWorkerControl.objects.filter(setup=strategy_setup,source=instance_id,
            account_login=account_login,server_name=server_name,symbol=decision.symbol,automatic=True).first()
        if not control: return None, False, 'STRATEGY_PAUSED_OR_SCOPE_MISMATCH'
        if not verified_engine_positions(heartbeat): return None, False, 'STRATEGY_HEDGING_OWNERSHIP_REQUIRED'
        if any(p.get('magic')==STRATEGIES[strategy_setup][2] for p in positions):
            return None, False, 'STRATEGY_POSITION_ALREADY_OPEN'
        own = strategy_commands(heartbeat,strategy_setup)
        if own.filter(action='OPEN',state__in=['PENDING','CLAIMED','RECEIVED','EXECUTING','RECONCILIATION_REQUIRED']).exists():
            # Return the existing same-signal command below, never create another entry.
            if not own.filter(action='OPEN',decision_id=decision.decision_id).exists():
                return None, False, 'STRATEGY_COMMAND_UNSETTLED'
        if own.filter(action='OPEN',state='EXECUTED',completed_at__gte=heartbeat.last_seen).exists():
            return None, False, 'STRATEGY_FILL_NOT_YET_OBSERVED'
    from .news_lock import entry_status, audit_block
    news = entry_status(strategy_setup or 'LEGACY', now=now, check_signal=True,
                        signal_bar_time=decision.features.get('market_snapshot',{}).get('bar_time'))
    if news['blocked']:
        audit_block(heartbeat, news, phase='CREATION', now=now)
        return None, False, news['reason']
    if not isinstance(positions, list) or (positions and not (
        verified_engine_positions(heartbeat) if strategy_setup else allow_main_alongside_scalps(heartbeat))):
        return None, False, "OPEN_POSITION_RISK_UNVERIFIED"
    if not isinstance(account, dict) or not isinstance(quote, dict):
        return None, False, "ACCOUNT_SNAPSHOT_INVALID"
    quote_time = parse_datetime(str(snapshot.get("quote_time", "")))
    if quote_time is None or quote_time.tzinfo is None or quote_time.utcoffset() is None:
        return None, False, "QUOTE_TIMESTAMP_INVALID"
    quote_age = (now - quote_time).total_seconds()
    if quote_age < -5 or quote_age > 60:
        return None, False, "QUOTE_STALE"
    try:
        bid = _decimal(quote.get("bid"), "bid")
        ask = _decimal(quote.get("ask"), "ask")
        current_balance = _decimal(account.get("balance"), "balance")
        baseline_equity = _decimal(account.get("equity"), "equity")
        decision_account = decision.features.get("account_snapshot", {})
        decision_balance = _decimal(decision_account.get("balance"), "decision balance")
        decision_equity = _decimal(decision_account.get("equity"), "decision equity")
        volume = _decimal(verdict.approved_volume, "approved_volume")
        stop_loss = _decimal(verdict.stop_loss, "stop_loss")
        take_profit = _decimal(verdict.take_profit, "take_profit")
    except (ValueError, TypeError, InvalidOperation):
        return None, False, "COMMAND_RISK_VALUES_INVALID"

    if bid <= 0 or ask < bid or current_balance <= 0 or baseline_equity <= 0 or volume <= 0:
        return None, False, "ACCOUNT_OR_QUOTE_VALUES_INVALID"
    if abs(current_balance - decision_balance) > Decimal("0.01"):
        return None, False, "ACCOUNT_BALANCE_CHANGED_AFTER_DECISION"
    if abs(baseline_equity - decision_equity) > Decimal("0.01"):
        return None, False, "ACCOUNT_EQUITY_CHANGED_AFTER_DECISION"

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
    try:
        tick_size = _decimal(snapshot.get("symbol_info", {}).get("tick_size"), "tick size")
        tick_value = _decimal(snapshot.get("symbol_info", {}).get("tick_value"), "tick value")
        if min(tick_size,tick_value)<=0: raise ValueError("contract")
        reserved_risk = risk_distance/tick_size*tick_value*volume
        if strategy_setup:
            per_trade_cap = _decimal(decision.features.get('risk_policy',{}).get('maximum_risk_percent'), 'per-trade cap')
            if per_trade_cap<=0 or reserved_risk>current_balance*per_trade_cap/100:
                return None, False, 'STRATEGY_RISK_CHANGED_AFTER_SIZING'
        if not EACommand.objects.filter(command_id=command_id).exists():
            from .scalper_engine import exposure_money, limits
            cap, _ = limits()
            if exposure_money(heartbeat)+reserved_risk > current_balance*cap/100:
                return None, False, "PORTFOLIO_RISK_LIMIT_REACHED"
    except (ValueError,TypeError,ArithmeticError):
        return None, False, "PORTFOLIO_RISK_UNVERIFIED"
    payload = {
        "reserved_risk_money": float(reserved_risk),
        "server_name": server_name,
        "side": decision.action,
        "volume": float(volume),
        "stop_loss": float(stop_loss),
        "take_profit": float(take_profit),
    }
    if strategy_setup:
        payload.update(engine='STRATEGY',strategy_setup=strategy_setup,magic=STRATEGIES[strategy_setup][2])
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
                "evaluation": evaluation,
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
