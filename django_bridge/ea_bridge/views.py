import json
from datetime import datetime, timezone
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from .auth import BearerTokenRequired
from .models import EAEvent, EAHeartbeat


def _body(request):
    if len(request.body) > settings.EA_MAX_BODY_BYTES:
        raise ValueError("request body too large")
    value = json.loads(request.body or b"{}")
    if not isinstance(value, dict):
        raise ValueError("JSON object required")
    return value

@require_GET
def handshake(request):
    denied = BearerTokenRequired.reject_if_invalid(request)
    if denied:
        return denied
    return JsonResponse({"ok": True, "protocol": "ramon-ea-v1", "server_time_utc": datetime.now(timezone.utc).isoformat(), "trading_authorization": False})

@csrf_exempt
@require_POST
def heartbeat(request):
    denied = BearerTokenRequired.reject_if_invalid(request)
    if denied:
        return denied
    try:
        p = _body(request)
        instance_id = str(p.get("instance_id", "")).strip()[:128]
        if not instance_id:
            return JsonResponse({"ok": False, "error": "instance_id required"}, status=400)
        row, _ = EAHeartbeat.objects.update_or_create(instance_id=instance_id, defaults={
            "account_login": str(p.get("account_login", ""))[:32],
            "server_name": str(p.get("server_name", ""))[:128],
            "symbol": str(p.get("symbol", ""))[:32],
            "ea_version": str(p.get("ea_version", ""))[:32],
            "status": str(p.get("status", "UNKNOWN"))[:64],
            "terminal_connected": bool(p.get("terminal_connected", False)),
            "trade_allowed": bool(p.get("trade_allowed", False)),
            "payload": p,
        })
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    return JsonResponse({"ok": True, "instance_id": row.instance_id, "received_at": row.last_seen.isoformat()})

@csrf_exempt
@require_POST
def events(request):
    denied = BearerTokenRequired.reject_if_invalid(request)
    if denied:
        return denied
    try:
        p = _body(request)
        instance_id = str(p.get("instance_id", "")).strip()[:128]
        event_id = str(p.get("event_id", "")).strip()[:128]
        event_type = str(p.get("event_type", "")).strip().upper()[:64]
        if not instance_id or not event_id or not event_type:
            return JsonResponse({"ok": False, "error": "instance_id, event_id and event_type required"}, status=400)
        occurred = p.get("occurred_at")
        occurred_at = datetime.fromisoformat(occurred.replace("Z", "+00:00")) if isinstance(occurred, str) and occurred else None
        row, created = EAEvent.objects.get_or_create(event_id=event_id, defaults={"instance_id": instance_id, "event_type": event_type, "occurred_at": occurred_at, "payload": p})
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    return JsonResponse({"ok": True, "accepted": created, "event_id": row.event_id})
