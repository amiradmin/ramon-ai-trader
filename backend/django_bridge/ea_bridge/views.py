import json
import logging
from datetime import datetime, timezone
from django.conf import settings
from django.http import JsonResponse, HttpResponse
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from .auth import BearerTokenRequired
from .models import EAEvent, EAHeartbeat, StrategyDecisionRecord
from .market_bars import validate_bar_timestamp

log = logging.getLogger(__name__)

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

@require_GET
def status(request):
    denied = BearerTokenRequired.reject_if_invalid(request)
    if denied:
        return denied
    now = datetime.now(timezone.utc)
    rows = []
    for row in EAHeartbeat.objects.order_by("-last_seen")[:100]:
        age = max(0, int((now - row.last_seen).total_seconds()))
        rows.append({"instance_id": row.instance_id, "symbol": row.symbol, "ea_version": row.ea_version, "status": row.status, "terminal_connected": row.terminal_connected, "trade_allowed": row.trade_allowed, "last_seen_utc": row.last_seen.isoformat(), "age_seconds": age, "connected": age <= 90})
    return JsonResponse({"ok": True, "instances": rows})

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
            "account_login": str(p.get("account_login", ""))[:32], "server_name": str(p.get("server_name", ""))[:128],
            "symbol": str(p.get("symbol", ""))[:32], "ea_version": str(p.get("ea_version", ""))[:32],
            "status": str(p.get("status", "UNKNOWN"))[:64], "terminal_connected": bool(p.get("terminal_connected", False)),
            "trade_allowed": bool(p.get("trade_allowed", False)), "payload": p})
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
        occurred_at = datetime.fromisoformat(occurred.replace("Z", "+00:00")) if isinstance(occurred, str) and occurred else datetime.now(timezone.utc)
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            return JsonResponse({"ok": False, "error": "occurred_at must include a timezone"}, status=400)

        # Opt-in rollout: the old durable DB ingestion remains the default.
        # When enabled, return success only after Redis accepted the event.
        if settings.RAMON_PIPELINE_INGEST_ENABLED:
            from ramon_pipeline.django_adapter import build_pipeline
            from ramon_pipeline.schemas import Envelope, EventKind
            upper = event_type.upper()
            if upper in {"MARKET_BAR", "BAR", "OHLCV"}:
                kind = EventKind.MARKET_BAR
                if all(key in p for key in ("symbol", "timeframe", "timestamp")):
                    bar_time = parse_datetime(str(p["timestamp"]))
                    if bar_time is None or bar_time.tzinfo is None or bar_time.utcoffset() is None:
                        return JsonResponse({"ok": False, "error": "market bar timestamp must include timezone"}, status=400)
                    try:
                        validate_bar_timestamp(bar_time, p["timeframe"])
                    except ValueError as exc:
                        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
            elif upper in {"MARKET_TICK", "TICK", "QUOTE"}:
                kind = EventKind.MARKET_TICK
            elif upper.startswith(("EXECUTION", "ORDER_", "TRADE_", "POSITION_", "FILL", "DEAL_")) or upper in {"FILL", "REJECTED", "CLOSED"}:
                kind = EventKind.EXECUTION_EVENT
            else:
                kind = EventKind.TELEMETRY
            envelope = Envelope(kind=kind, source=instance_id, payload=p, event_id=event_id, occurred_at=occurred_at)
            try:
                stream_id = build_pipeline().accept(envelope)
            except Exception:
                log.exception("Ramon Redis ingestion unavailable; event not acknowledged event_id=%s", event_id)
                response = JsonResponse({"ok": False, "error": "ingestion temporarily unavailable; retry event"}, status=503)
                response["Retry-After"] = "2"
                return response
            return JsonResponse({"ok": True, "accepted": True, "queued": True, "event_id": event_id, "stream_id": stream_id})

        row, created = EAEvent.objects.get_or_create(event_id=event_id, defaults={"instance_id": instance_id, "event_type": event_type, "occurred_at": occurred_at, "payload": p})
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    return JsonResponse({"ok": True, "accepted": created, "event_id": row.event_id})


@require_GET
def dashboard(request):
    """Render the original read-only Ramon opportunity dashboard."""
    from django.shortcuts import render
    return render(request, "ea_bridge/ramon_opportunity_risk_dashboard.html")


@require_GET
def dashboard_data(request):
    """Read-only JSON feed for the local Ramon dashboard."""
    rows = StrategyDecisionRecord.objects.order_by("-created_at")[:50]
    decisions = []
    for row in rows:
        decisions.append({
            "decision_id": row.decision_id,
            "symbol": row.symbol,
            "action": row.action,
            "confidence": row.confidence,
            "rationale_code": row.rationale_code,
            "model_version": row.model_version,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "features": row.features if isinstance(row.features, dict) else {},
        })
    return JsonResponse({"ok": True, "decisions": decisions})
