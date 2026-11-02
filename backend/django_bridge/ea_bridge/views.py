import json
import logging
from datetime import datetime, timezone
from django.conf import settings
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from .auth import BearerTokenRequired
from .models import EAEvent, EAHeartbeat, StrategyDecisionRecord

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



def _dashboard_execution_state(latest_symbol=""):
    """Return read-only, current execution-boundary telemetry for the dashboard."""
    now = datetime.now(timezone.utc)
    heartbeat = EAHeartbeat.objects.order_by("-last_seen").first()
    max_age = int(getattr(settings, "RAMON_EA_HEARTBEAT_MAX_AGE_SECONDS", 90))
    heartbeat_age = None
    heartbeat_fresh = None
    terminal_connected = None
    trade_allowed = None
    demo_account = None
    symbol_matches = None

    if heartbeat is not None:
        last_seen = heartbeat.last_seen
        if last_seen.tzinfo is None or last_seen.utcoffset() is None:
            last_seen = last_seen.replace(tzinfo=timezone.utc)
        heartbeat_age = max(0, int((now - last_seen).total_seconds()))
        heartbeat_fresh = heartbeat_age <= max_age
        terminal_connected = bool(heartbeat.terminal_connected)
        trade_allowed = bool(heartbeat.trade_allowed)
        payload = heartbeat.payload if isinstance(heartbeat.payload, dict) else {}
        trade_mode = payload.get("account_trade_mode")
        if isinstance(trade_mode, str):
            demo_account = trade_mode.strip().upper() == "DEMO"
        elif isinstance(trade_mode, int) and not isinstance(trade_mode, bool):
            demo_account = trade_mode == 0
        if latest_symbol and heartbeat.symbol:
            symbol_matches = heartbeat.symbol.strip().upper() == latest_symbol.strip().upper()

    command_creation = bool(getattr(settings, "RAMON_EA_COMMAND_CREATION_ENABLED", False))
    command_execution = bool(getattr(settings, "RAMON_EA_COMMAND_EXECUTION_ENABLED", False))

    def check(name, label, passed, detail, metrics=None):
        status = "ACCEPTED" if passed is True else "BLOCKED" if passed is False else "WAITING"
        item = {
            "name": name,
            "display_name": label,
            "passed": passed,
            "status": status,
            "detail": detail,
        }
        if metrics:
            item["metrics"] = metrics
        return item

    # The dashboard feed is read-only; that does not mean the EA execution
    # path is disabled. Django may authorize only a specific, risk-approved
    # command after all account/terminal prerequisites are satisfied.
    authorization_ready = all((
        command_creation,
        command_execution,
        heartbeat_fresh is True,
        terminal_connected is True,
        trade_allowed is True,
        demo_account is True,
        symbol_matches is True,
    ))

    checks = [
        check(
            "django_authorization",
            "Django command authorization",
            authorization_ready,
            (
                "Django command authorization is ready. Individual OPEN commands still require "
                "a fresh approved strategy decision, a passing risk verdict, and server-side "
                "account validation; dashboard and decision responses do not execute trades."
                if authorization_ready else
                "Command authorization is not ready. Both server command gates, a fresh "
                "connected demo-account heartbeat, terminal trading permission, and a matching "
                "EA/decision symbol are required."
            ),
        ),
        check(
            "command_creation",
            "Server command-creation gate",
            command_creation,
            "RAMON_EA_COMMAND_CREATION_ENABLED is " + ("enabled." if command_creation else "disabled."),
        ),
        check(
            "command_execution",
            "Server command-execution gate",
            command_execution,
            "RAMON_EA_COMMAND_EXECUTION_ENABLED is " + ("enabled." if command_execution else "disabled."),
        ),
        check(
            "ea_heartbeat",
            "EA heartbeat freshness",
            heartbeat_fresh,
            "No EA heartbeat has been received." if heartbeat is None else
            "Latest heartbeat age is " + str(heartbeat_age) + " seconds; maximum accepted age is " + str(max_age) + " seconds.",
            {"age_seconds": heartbeat_age, "maximum_age_seconds": max_age},
        ),
        check(
            "terminal_connected",
            "MT5 terminal connection",
            terminal_connected,
            "No heartbeat is available." if heartbeat is None else
            ("MT5 terminal reports connected." if terminal_connected else "MT5 terminal reports disconnected."),
        ),
        check(
            "terminal_trade_allowed",
            "Terminal Algo Trading permission",
            trade_allowed,
            "No heartbeat is available." if heartbeat is None else
            ("Terminal reports trading permission enabled." if trade_allowed else "Terminal reports trading permission disabled."),
        ),
        check(
            "demo_account",
            "Demo-account mode",
            demo_account,
            "Account trade mode was not supplied by the latest heartbeat." if demo_account is None else
            ("Heartbeat confirms demo-account mode." if demo_account else "Heartbeat does not identify a demo account."),
        ),
        check(
            "symbol_match",
            "EA / decision symbol match",
            symbol_matches,
            "A current decision symbol and EA heartbeat symbol are both required." if symbol_matches is None else
            ("EA symbol matches the latest decision." if symbol_matches else "EA symbol does not match the latest decision."),
        ),
    ]
    return {
        "title": "Execution boundary",
        "trading_authorization": authorization_ready,
        "authorization_detail": (
            "Django command authorization is ready; only an individually risk-approved, "
            "account-scoped pending command can authorize MT5 execution."
            if authorization_ready else
            "Django command authorization is not ready; one or more execution prerequisites are blocked."
        ),
        "command_creation_enabled": command_creation,
        "command_execution_enabled": command_execution,
        "heartbeat_age_seconds": heartbeat_age,
        "heartbeat_fresh": heartbeat_fresh,
        "terminal_connected": terminal_connected,
        "terminal_trade_allowed": trade_allowed,
        "demo_account_confirmed": demo_account,
        "symbol_matches": symbol_matches,
        "checks": checks,
    }


@require_GET
def dashboard_data(request):
    """Read-only JSON feed for the local Ramon dashboard."""
    rows = list(StrategyDecisionRecord.objects.order_by("-created_at")[:50])
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
    latest_symbol = decisions[0]["symbol"] if decisions else ""
    return JsonResponse({
        "ok": True,
        "decisions": decisions,
        "execution": _dashboard_execution_state(latest_symbol),
    })
