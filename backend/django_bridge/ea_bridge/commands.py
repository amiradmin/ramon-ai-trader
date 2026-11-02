"""Authenticated command polling and acknowledgement endpoints.

Delivery is fail-closed: both feature flags and fresh broker telemetry are
required. OPEN commands are revalidated against their persisted decision,
approved risk verdict, current account snapshot, and daily baseline at claim
time. Telemetry never grants authorization by itself.
"""
import json
from datetime import date
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from ramon_pipeline.signalflow.risk_management import RiskConfig

from .auth import BearerTokenRequired
from .models import (
    AccountDailyBaseline,
    EACommand,
    EAHeartbeat,
    RiskVerdictRecord,
    StrategyDecisionRecord,
)


TERMINAL_STATES = {
    EACommand.State.EXECUTED,
    EACommand.State.REJECTED,
    EACommand.State.FAILED,
    EACommand.State.EXPIRED,
    EACommand.State.RECONCILIATION_REQUIRED,
}
ALLOWED_ACK_STATES = TERMINAL_STATES | {
    EACommand.State.RECEIVED,
    EACommand.State.EXECUTING,
}
MAX_REJECTIONS_PER_POLL = 25
HEARTBEAT_MAX_AGE_SECONDS = 90
QUOTE_MAX_AGE_SECONDS = 60
QUOTE_FUTURE_TOLERANCE_SECONDS = 5


def _body(request):
    limit = getattr(settings, "EA_MAX_BODY_BYTES", 65536)
    if len(request.body) > limit:
        raise ValueError("request body too large")
    value = json.loads(request.body or b"{}")
    if not isinstance(value, dict):
        raise ValueError("JSON object required")
    return value


def _decimal(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("not numeric") from None
    if not result.is_finite():
        raise ValueError("not finite")
    return result


def _within(actual, expected, tolerance):
    return abs(actual - expected) <= tolerance


def _delivery_block_reason(
    *, heartbeat, command, instance_id, account_login, requested_server_name,
    requested_symbol, now,
):
    """Revalidate broker state and the approved OPEN risk chain before claim."""
    if heartbeat is None:
        return "ACCOUNT_TELEMETRY_UNAVAILABLE"

    age = (now - heartbeat.last_seen).total_seconds()
    if age < 0 or age > HEARTBEAT_MAX_AGE_SECONDS:
        return "ACCOUNT_TELEMETRY_STALE"
    if heartbeat.instance_id != instance_id or heartbeat.account_login != account_login:
        return "ACCOUNT_IDENTITY_MISMATCH"
    if not heartbeat.server_name or not heartbeat.symbol:
        return "ACCOUNT_IDENTITY_INCOMPLETE"
    if requested_server_name and heartbeat.server_name != requested_server_name:
        return "ACCOUNT_SERVER_MISMATCH"
    if requested_symbol and heartbeat.symbol != requested_symbol:
        return "ACCOUNT_SYMBOL_MISMATCH"
    if command.symbol != heartbeat.symbol:
        return "ACCOUNT_SYMBOL_MISMATCH"
    if heartbeat.status != "TELEMETRY_OK" or not heartbeat.terminal_connected:
        return "ACCOUNT_TELEMETRY_NOT_READY"
    if not heartbeat.trade_allowed:
        return "TERMINAL_TRADE_PERMISSION_DENIED"

    snapshot = heartbeat.payload if isinstance(heartbeat.payload, dict) else {}
    if snapshot.get("server_name") != heartbeat.server_name:
        return "ACCOUNT_SERVER_MISMATCH"
    trade_mode = snapshot.get("account_trade_mode")
    if type(trade_mode) is not int or trade_mode != 0:
        return "DEMO_ACCOUNT_REQUIRED"

    positions = snapshot.get("positions")
    account = snapshot.get("account")
    quote = snapshot.get("quote")
    if not isinstance(positions, list):
        return "POSITIONS_SNAPSHOT_INVALID"
    if not isinstance(account, dict) or not isinstance(quote, dict):
        return "ACCOUNT_SNAPSHOT_INVALID"
    if command.action == EACommand.Action.OPEN and positions:
        return "OPEN_POSITION_RISK_UNVERIFIED"

    quote_time = parse_datetime(str(snapshot.get("quote_time", "")))
    if quote_time is None or quote_time.tzinfo is None or quote_time.utcoffset() is None:
        return "QUOTE_TIMESTAMP_INVALID"
    quote_age = (now - quote_time).total_seconds()
    if quote_age < -QUOTE_FUTURE_TOLERANCE_SECONDS or quote_age > QUOTE_MAX_AGE_SECONDS:
        return "QUOTE_STALE"

    try:
        bid = _decimal(quote.get("bid"))
        ask = _decimal(quote.get("ask"))
        balance = _decimal(account.get("balance"))
        equity = _decimal(account.get("equity"))
    except ValueError:
        return "ACCOUNT_OR_QUOTE_VALUES_INVALID"
    if bid <= 0 or ask < bid or balance <= 0 or equity <= 0:
        return "ACCOUNT_OR_QUOTE_VALUES_INVALID"

    # Closing/modifying an existing position must not be blocked by a missing
    # daily baseline. The baseline and full risk-chain checks apply to new risk.
    if command.action != EACommand.Action.OPEN:
        return ""

    broker_day_raw = snapshot.get("broker_day")
    if not isinstance(broker_day_raw, str):
        return "DAILY_LOSS_BASELINE_UNAVAILABLE"
    try:
        broker_day = date.fromisoformat(broker_day_raw)
    except ValueError:
        return "BROKER_DAY_INVALID"
    if broker_day.isoformat() != broker_day_raw:
        return "BROKER_DAY_INVALID"

    baseline = AccountDailyBaseline.objects.filter(
        account_login=account_login,
        server_name=heartbeat.server_name,
        broker_day=broker_day,
    ).first()
    if baseline is None or baseline.opening_balance <= 0:
        return "DAILY_LOSS_BASELINE_UNAVAILABLE"

    # Verify the exact persisted strategy/risk chain used to create this command.
    decision = StrategyDecisionRecord.objects.filter(
        decision_id=command.decision_id,
        symbol=command.symbol,
    ).first()
    if decision is None or decision.action not in ("BUY", "SELL"):
        return "STRATEGY_DECISION_UNAVAILABLE"
    try:
        verdict = RiskVerdictRecord.objects.filter(
            pk=command.risk_verdict_id,
            decision_id=command.decision_id,
            approved=True,
        ).first()
    except (TypeError, ValueError):
        verdict = None
    if verdict is None:
        return "APPROVED_RISK_VERDICT_UNAVAILABLE"
    if verdict.approved_volume is None or verdict.stop_loss is None or verdict.take_profit is None:
        return "APPROVED_RISK_VALUES_INCOMPLETE"

    details = verdict.details if isinstance(verdict.details, dict) else {}
    if details.get("daily_baseline_day") != broker_day.isoformat():
        return "RISK_VERDICT_BASELINE_DAY_MISMATCH"

    features = decision.features if isinstance(decision.features, dict) else {}
    decision_account = features.get("account_snapshot")
    if not isinstance(decision_account, dict):
        return "DECISION_ACCOUNT_SNAPSHOT_UNAVAILABLE"
    try:
        decision_balance = _decimal(decision_account.get("balance"))
        decision_equity = _decimal(decision_account.get("equity"))
        approved_volume = _decimal(verdict.approved_volume)
        stop_loss = _decimal(verdict.stop_loss)
        take_profit = _decimal(verdict.take_profit)
        payload = command.payload if isinstance(command.payload, dict) else {}
        payload_volume = _decimal(payload.get("volume"))
        payload_stop = _decimal(payload.get("stop_loss"))
        payload_target = _decimal(payload.get("take_profit"))
    except ValueError:
        return "APPROVED_RISK_VALUES_INVALID"

    if not _within(balance, decision_balance, Decimal("0.01")):
        return "ACCOUNT_BALANCE_CHANGED_AFTER_DECISION"
    if not _within(equity, decision_equity, Decimal("0.01")):
        return "ACCOUNT_EQUITY_CHANGED_AFTER_DECISION"
    if approved_volume <= 0 or not _within(payload_volume, approved_volume, Decimal("0.00000001")):
        return "COMMAND_VOLUME_DOES_NOT_MATCH_RISK_VERDICT"
    if not _within(payload_stop, stop_loss, Decimal("0.0000000001")):
        return "COMMAND_STOP_LOSS_DOES_NOT_MATCH_RISK_VERDICT"
    if not _within(payload_target, take_profit, Decimal("0.0000000001")):
        return "COMMAND_TAKE_PROFIT_DOES_NOT_MATCH_RISK_VERDICT"
    if payload.get("side") != decision.action:
        return "COMMAND_SIDE_DOES_NOT_MATCH_DECISION"

    daily_loss_percent = max(
        Decimal("0"),
        (baseline.opening_balance - equity) / baseline.opening_balance * Decimal("100"),
    )
    if daily_loss_percent >= Decimal(str(RiskConfig().max_daily_loss_percent)):
        return "DAILY_LOSS_LIMIT_REACHED"

    if decision.action == "BUY":
        risk_distance, reward_distance = ask - stop_loss, take_profit - ask
    else:
        risk_distance, reward_distance = stop_loss - bid, bid - take_profit
    if risk_distance <= 0 or reward_distance <= 0:
        return "INVALID_STOP_OR_TARGET"
    reward_risk = reward_distance / risk_distance
    if reward_risk < Decimal(str(RiskConfig().min_reward_risk)):
        return "REWARD_RISK_TOO_LOW"

    return ""


@csrf_exempt
@require_POST
def next_command(request):
    denied = BearerTokenRequired.reject_if_invalid(request)
    if denied:
        return denied
    try:
        p = _body(request)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({"ok": False, "error": "invalid JSON object or request too large"}, status=400)

    instance_id = str(p.get("instance_id", "")).strip()
    account_login = str(p.get("account_login", "")).strip()
    requested_server_name = str(p.get("server_name", "") or "").strip()
    requested_symbol = str(p.get("symbol", "") or "").strip()
    if (
        not instance_id or len(instance_id) > 128
        or not account_login or len(account_login) > 32
        or len(requested_server_name) > 128
        or len(requested_symbol) > 32
    ):
        return JsonResponse({
            "ok": False,
            "error": "valid instance_id and account_login required; optional server_name and symbol must be valid",
        }, status=400)

    creation_enabled = getattr(settings, "RAMON_EA_COMMAND_CREATION_ENABLED", False)
    execution_enabled = getattr(settings, "RAMON_EA_COMMAND_EXECUTION_ENABLED", False)
    if not (creation_enabled and execution_enabled):
        return JsonResponse({
            "ok": True,
            "commands_enabled": False,
            "command": None,
            "trading_authorization": False,
            "reason": "COMMAND_CREATION_OR_EXECUTION_DISABLED",
        })

    now = timezone.now()
    rejected = []
    with transaction.atomic():
        EACommand.objects.filter(
            instance_id=instance_id,
            account_login=account_login,
            state=EACommand.State.PENDING,
            expires_at__lte=now,
        ).update(
            state=EACommand.State.EXPIRED,
            completed_at=now,
            result={"reason": "COMMAND_EXPIRED_BEFORE_DELIVERY"},
        )

        # EAHeartbeat.instance_id is unique. Locking this row serializes delivery
        # for one EA/account while telemetry refreshes the same record.
        heartbeat = (
            EAHeartbeat.objects.select_for_update()
            .filter(instance_id=instance_id)
            .first()
        )

        for _ in range(MAX_REJECTIONS_PER_POLL + 1):
            command = (
                EACommand.objects.select_for_update()
                .filter(
                    instance_id=instance_id,
                    account_login=account_login,
                    state=EACommand.State.PENDING,
                    expires_at__gt=now,
                    execution_authorized=True,
                )
                .order_by("created_at", "pk")
                .first()
            )
            if command is None:
                return JsonResponse({
                    "ok": True,
                    "commands_enabled": True,
                    "command": None,
                    "trading_authorization": False,
                    "rejected_commands": rejected,
                })

            reason = _delivery_block_reason(
                heartbeat=heartbeat,
                command=command,
                instance_id=instance_id,
                account_login=account_login,
                requested_server_name=requested_server_name,
                requested_symbol=requested_symbol,
                now=now,
            )
            if reason:
                command.state = EACommand.State.REJECTED
                command.completed_at = now
                command.result = {"reason": reason, "stage": "DELIVERY_REVALIDATION"}
                command.save(update_fields=["state", "completed_at", "result"])
                rejected.append({"command_id": command.command_id, "reason": reason})
                if len(rejected) >= MAX_REJECTIONS_PER_POLL:
                    break
                continue

            command.state = EACommand.State.CLAIMED
            command.claimed_at = now
            command.save(update_fields=["state", "claimed_at"])
            return JsonResponse({
                "ok": True,
                "commands_enabled": True,
                "trading_authorization": True,
                "rejected_commands": rejected,
                "command": {
                    "command_id": command.command_id,
                    "instance_id": command.instance_id,
                    "account_login": command.account_login,
                    "symbol": command.symbol,
                    "action": command.action,
                    "payload": command.payload,
                    "decision_id": command.decision_id,
                    "risk_verdict_id": command.risk_verdict_id,
                    "execution_authorized": command.execution_authorized,
                    "created_at": command.created_at.isoformat(),
                    "expires_at": command.expires_at.isoformat(),
                },
            })

    return JsonResponse({
        "ok": True,
        "commands_enabled": True,
        "command": None,
        "trading_authorization": False,
        "rejected_commands": rejected,
        "reason": "REJECTION_LIMIT_REACHED",
    })


@csrf_exempt
@require_POST
def acknowledge_command(request):
    denied = BearerTokenRequired.reject_if_invalid(request)
    if denied:
        return denied
    try:
        p = _body(request)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({"ok": False, "error": "invalid JSON object or request too large"}, status=400)

    command_id = str(p.get("command_id", "")).strip()
    instance_id = str(p.get("instance_id", "")).strip()
    account_login = str(p.get("account_login", "")).strip()
    state = str(p.get("state", "")).strip().upper()
    result = p.get("result", {})
    if not command_id or not instance_id or not account_login:
        return JsonResponse({"ok": False, "error": "command_id, instance_id and account_login required"}, status=400)
    if state not in ALLOWED_ACK_STATES:
        return JsonResponse({"ok": False, "error": "unsupported command state"}, status=400)
    if not isinstance(result, dict):
        return JsonResponse({"ok": False, "error": "result must be a JSON object"}, status=400)

    now = timezone.now()
    with transaction.atomic():
        try:
            command = EACommand.objects.select_for_update().get(
                command_id=command_id,
                instance_id=instance_id,
                account_login=account_login,
            )
        except EACommand.DoesNotExist:
            return JsonResponse({"ok": False, "error": "command not found for this instance/account"}, status=404)

        if command.state in TERMINAL_STATES:
            if command.state == state and command.result == result:
                return JsonResponse({"ok": True, "duplicate": True, "state": command.state})
            return JsonResponse({
                "ok": False, "error": "command is already in a terminal state",
                "state": command.state,
            }, status=409)

        valid_transitions = {
            EACommand.State.PENDING: {EACommand.State.REJECTED, EACommand.State.EXPIRED},
            EACommand.State.CLAIMED: ALLOWED_ACK_STATES,
            EACommand.State.RECEIVED: ALLOWED_ACK_STATES - {EACommand.State.RECEIVED},
            EACommand.State.EXECUTING: ALLOWED_ACK_STATES - {EACommand.State.RECEIVED, EACommand.State.EXECUTING},
        }
        if state not in valid_transitions.get(command.state, set()):
            return JsonResponse({
                "ok": False, "error": "invalid command state transition",
                "state": command.state,
            }, status=409)

        command.state = state
        command.result = result
        fields = ["state", "result"]
        if state in TERMINAL_STATES:
            command.completed_at = now
            fields.append("completed_at")
        command.save(update_fields=fields)
    return JsonResponse({"ok": True, "duplicate": False, "state": state})
