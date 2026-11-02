"""Authenticated command polling and acknowledgement endpoints.

No endpoint creates a trade command. Command creation must be wired to an
explicitly approved strategy + risk-verdict path in a later integration step.
Execution is disabled unless RAMON_EA_COMMAND_EXECUTION_ENABLED is true.
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

from .auth import BearerTokenRequired
from .models import AccountDailyBaseline, EACommand, EAHeartbeat


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


def _body(request):
    limit = getattr(settings, "EA_MAX_BODY_BYTES", 65536)
    if len(request.body) > limit:
        raise ValueError("request body too large")
    value = json.loads(request.body or b"{}")
    if not isinstance(value, dict):
        raise ValueError("JSON object required")
    return value


def _delivery_block_reason(*, heartbeat, command, instance_id, account_login, server_name, symbol, now):
    """Revalidate broker state at claim time; never trust creation-time telemetry alone."""
    if heartbeat is None:
        return "ACCOUNT_TELEMETRY_UNAVAILABLE"
    age = (now - heartbeat.last_seen).total_seconds()
    if age < 0 or age > 90:
        return "ACCOUNT_TELEMETRY_STALE"
    if heartbeat.instance_id != instance_id or heartbeat.account_login != account_login:
        return "ACCOUNT_IDENTITY_MISMATCH"
    if not server_name or heartbeat.server_name != server_name:
        return "ACCOUNT_SERVER_MISMATCH"
    if not symbol or heartbeat.symbol != symbol or command.symbol != symbol:
        return "ACCOUNT_SYMBOL_MISMATCH"
    if heartbeat.status != "TELEMETRY_OK" or not heartbeat.terminal_connected:
        return "ACCOUNT_TELEMETRY_NOT_READY"
    if not heartbeat.trade_allowed:
        return "TERMINAL_TRADE_PERMISSION_DENIED"

    snapshot = heartbeat.payload if isinstance(heartbeat.payload, dict) else {}
    if snapshot.get("server_name") != server_name:
        return "ACCOUNT_SERVER_MISMATCH"
    if type(snapshot.get("account_trade_mode")) is not int or snapshot.get("account_trade_mode") != 0:
        return "DEMO_ACCOUNT_REQUIRED"
    positions = snapshot.get("positions")
    account = snapshot.get("account")
    quote = snapshot.get("quote")
    if not isinstance(positions, list):
        return "POSITIONS_SNAPSHOT_INVALID"
    if command.action == EACommand.Action.OPEN and positions:
        return "OPEN_POSITION_RISK_UNVERIFIED"
    if not isinstance(account, dict) or not isinstance(quote, dict):
        return "ACCOUNT_SNAPSHOT_INVALID"

    quote_time = parse_datetime(str(snapshot.get("quote_time", "")))
    if quote_time is None or quote_time.tzinfo is None or quote_time.utcoffset() is None:
        return "QUOTE_TIMESTAMP_INVALID"
    quote_age = (now - quote_time).total_seconds()
    if quote_age < -5 or quote_age > 60:
        return "QUOTE_STALE"
    try:
        bid = Decimal(str(quote.get("bid")))
        ask = Decimal(str(quote.get("ask")))
        balance = Decimal(str(account.get("balance")))
        equity = Decimal(str(account.get("equity")))
    except (InvalidOperation, TypeError, ValueError):
        return "ACCOUNT_OR_QUOTE_VALUES_INVALID"
    if not all(value.is_finite() for value in (bid, ask, balance, equity)):
        return "ACCOUNT_OR_QUOTE_VALUES_INVALID"
    if bid <= 0 or ask < bid or balance <= 0 or equity <= 0:
        return "ACCOUNT_OR_QUOTE_VALUES_INVALID"

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
        server_name=server_name,
        broker_day=broker_day,
    ).first()
    if baseline is None or baseline.opening_balance <= 0:
        return "DAILY_LOSS_BASELINE_UNAVAILABLE"
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
    server_name = str(p.get("server_name", "")).strip()
    symbol = str(p.get("symbol", "")).strip()
    if (
        not instance_id or len(instance_id) > 128
        or not account_login or len(account_login) > 32
        or not server_name or len(server_name) > 128
        or not symbol or len(symbol) > 32
    ):
        return JsonResponse({
            "ok": False,
            "error": "valid instance_id, account_login, server_name and symbol required",
        }, status=400)

    creation_enabled = getattr(settings, "RAMON_EA_COMMAND_CREATION_ENABLED", False)
    execution_enabled = getattr(settings, "RAMON_EA_COMMAND_EXECUTION_ENABLED", False)
    if not (creation_enabled and execution_enabled):
        return JsonResponse({
            "ok": True, "commands_enabled": False, "command": None,
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

        heartbeat = EAHeartbeat.objects.select_for_update().filter(instance_id=instance_id).first()
        while True:
            command = (
                EACommand.objects.select_for_update()
                .filter(
                    instance_id=instance_id,
                    account_login=account_login,
                    state=EACommand.State.PENDING,
                    expires_at__gt=now,
                    execution_authorized=True,
                    symbol=symbol,
                )
                .order_by("created_at")
                .first()
            )
            if command is None:
                return JsonResponse({
                    "ok": True, "commands_enabled": True, "command": None,
                    "trading_authorization": False,
                    "rejected_commands": rejected,
                })

            reason = _delivery_block_reason(
                heartbeat=heartbeat,
                command=command,
                instance_id=instance_id,
                account_login=account_login,
                server_name=server_name,
                symbol=symbol,
                now=now,
            )
            if reason:
                command.state = EACommand.State.REJECTED
                command.completed_at = now
                command.result = {"reason": reason, "stage": "DELIVERY_REVALIDATION"}
                command.save(update_fields=["state", "completed_at", "result"])
                rejected.append({"command_id": command.command_id, "reason": reason})
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
