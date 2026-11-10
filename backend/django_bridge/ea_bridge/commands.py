"""Authenticated polling and acknowledgement for Django-authorized EA commands.

The decision pipeline may create an OPEN command only after strategy approval,
a passing risk verdict, and server-side account validation. This module delivers durable commands and invokes the separately gated Django
profit-protection planner for demo positions.
Execution remains gated by RAMON_EA_COMMAND_EXECUTION_ENABLED.
"""
import json

from django.conf import settings
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .auth import BearerTokenRequired
from .models import EACommand, EAHeartbeat


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
    if not instance_id or len(instance_id) > 128 or not account_login or len(account_login) > 32:
        return JsonResponse({"ok": False, "error": "valid instance_id and account_login required"}, status=400)

    if not getattr(settings, "RAMON_EA_COMMAND_EXECUTION_ENABLED", False):
        return JsonResponse({
            "ok": True, "commands_enabled": False, "command": None,
            "trading_authorization": False,
        })

    now = timezone.now()

    # Expire old rows even when the bridge is currently offline.
    expired_ids = list(EACommand.objects.filter(instance_id=instance_id,account_login=account_login,
        state=EACommand.State.PENDING,expires_at__lte=now,payload__engine='SCALPER').values_list('pk',flat=True))
    EACommand.objects.filter(instance_id=instance_id, account_login=account_login,
        state=EACommand.State.PENDING, expires_at__lte=now).update(
            state=EACommand.State.EXPIRED, completed_at=now
        )

    from .scalper_audit import command_event
    for expired in EACommand.objects.filter(pk__in=expired_ids,state=EACommand.State.EXPIRED):
        command_event(expired,'COMMAND_EXPIRY',now=now)

    # Django is the execution authority: revalidate the live bridge/account state.
    heartbeat = EAHeartbeat.objects.filter(instance_id=instance_id).first()
    if heartbeat is None:
        return JsonResponse({
            "ok": True, "commands_enabled": True, "command": None,
            "trading_authorization": False, "authorization_reason": "HEARTBEAT_MISSING",
        })
    payload = heartbeat.payload if isinstance(heartbeat.payload, dict) else {}
    trade_mode = payload.get("account_trade_mode")
    max_age = int(getattr(settings, "RAMON_EA_HEARTBEAT_MAX_AGE_SECONDS", 90))
    age = (now - heartbeat.last_seen).total_seconds()
    if age < 0 or age > max_age:
        reason = "HEARTBEAT_STALE"
    elif heartbeat.account_login != account_login:
        reason = "ACCOUNT_LOGIN_MISMATCH"
    elif not heartbeat.server_name or not str(payload.get("server_name", "")).strip() == heartbeat.server_name:
        reason = "ACCOUNT_SERVER_MISMATCH"
    elif heartbeat.status != "TELEMETRY_OK" or not heartbeat.terminal_connected:
        reason = "TERMINAL_NOT_READY"
    elif not heartbeat.trade_allowed:
        reason = "TERMINAL_TRADING_DISABLED"
    elif type(trade_mode) is not int or trade_mode != 0:
        reason = "DEMO_ACCOUNT_REQUIRED"
    else:
        reason = ""

    if reason:
        return JsonResponse({
            "ok": True, "commands_enabled": True, "command": None,
            "trading_authorization": False, "authorization_reason": reason,
        })

    from .profit_protection import create_protection_commands
    create_protection_commands(instance_id, account_login)

    with transaction.atomic():
        from .news_lock import entry_status, audit_block
        while True:
            command = (
                EACommand.objects.select_for_update()
                .filter(
                    instance_id=instance_id,
                    account_login=account_login,
                    symbol=heartbeat.symbol,
                    state=EACommand.State.PENDING,
                    expires_at__gt=now,
                    execution_authorized=True,
                )
                .order_by("created_at")
                .first()
            )
            if command is None:
                return JsonResponse({
                    "ok": True, "commands_enabled": True, "command": None,
                    "trading_authorization": False,
                })
            if command.action != EACommand.Action.OPEN:
                break
            setup = ('SCALPER_M1_M5' if command.payload.get('engine') == 'SCALPER'
                     else command.payload.get('strategy_setup', 'LEGACY'))
            features = command.evaluation.features if command.evaluation_id else {}
            news = entry_status(setup, now=timezone.now(), check_signal=True,
                                signal_bar_time=features.get('market_snapshot',{}).get('bar_time'))
            if not news['blocked']:
                break
            command.state = EACommand.State.REJECTED
            command.completed_at = timezone.now()
            command.result = {'detail': news['reason'], 'news_lock': news}
            command.save(update_fields=['state', 'completed_at', 'result'])
            audit_block(heartbeat, news, phase='DELIVERY', command_id=command.command_id)
            # Continue to exits/protection rather than letting blocked entries hide them.

        # Bind delivery to the broker server that approved the original decision.
        # Never substitute the latest heartbeat's server for a missing/mismatched
        # command server, even if an account login happens to match on both servers.
        command_payload = command.payload if isinstance(command.payload, dict) else {}
        expected_server = str(command_payload.get("server_name", "")).strip()
        if not expected_server or expected_server != heartbeat.server_name:
            command.state = EACommand.State.REJECTED
            command.result = {"detail": "COMMAND_SERVER_MISMATCH"}
            command.completed_at = now
            command.save(update_fields=["state", "result", "completed_at"])
            return JsonResponse({
                "ok": True, "commands_enabled": True, "command": None,
                "trading_authorization": False,
                "authorization_reason": "COMMAND_SERVER_MISMATCH",
            })

        command.state = EACommand.State.CLAIMED
        command.claimed_at = now
        command.save(update_fields=["state", "claimed_at"])
        from .scalper_audit import command_event
        command_event(command,"COMMAND_CLAIM",now=now)
        return JsonResponse({
            "ok": True,
            "commands_enabled": True,
            "trading_authorization": True,
            "command": {
                "command_id": command.command_id,
                "instance_id": command.instance_id,
                "account_login": command.account_login,
                "symbol": command.symbol,
                "server_name": expected_server,
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
        from .scalper_audit import command_event
        command_event(command,"BROKER_ACK",now=now)
    return JsonResponse({"ok": True, "duplicate": False, "state": state})
