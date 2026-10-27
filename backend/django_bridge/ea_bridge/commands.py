"""Authenticated command polling and acknowledgement endpoints.

No endpoint creates a trade command. Command creation must be wired to an
explicitly approved strategy + risk-verdict path in a later integration step.
Execution is disabled unless RAMON_EA_COMMAND_EXECUTION_ENABLED is true.
"""
import json

from django.conf import settings
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .auth import BearerTokenRequired
from .models import EACommand


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
    with transaction.atomic():
        EACommand.objects.filter(
            instance_id=instance_id,
            account_login=account_login,
            state=EACommand.State.PENDING,
            expires_at__lte=now,
        ).update(state=EACommand.State.EXPIRED, completed_at=now)

        command = (
            EACommand.objects.select_for_update()
            .filter(
                instance_id=instance_id,
                account_login=account_login,
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

        command.state = EACommand.State.CLAIMED
        command.claimed_at = now
        command.save(update_fields=["state", "claimed_at"])
        return JsonResponse({
            "ok": True,
            "commands_enabled": True,
            "trading_authorization": True,
            "command": {
                "command_id": command.command_id,
                "instance_id": command.instance_id,
                "account_login": command.account_login,
                "symbol": command.symbol,
                "action": command.action,
                "payload": command.payload,
                "decision_id": command.decision_id,
                "risk_verdict_id": command.risk_verdict_id,
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
