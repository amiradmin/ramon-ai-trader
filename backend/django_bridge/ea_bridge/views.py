import json
import logging
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from django.conf import settings
from django.http import JsonResponse, HttpResponse
from django.db.models import Avg, Count, Q, Sum
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from .auth import BearerTokenRequired
from .models import (EAEvent, EAHeartbeat, LearningTradeOutcome, MarketBar, StrategyDecisionRecord, RiskVerdictRecord, DecisionThresholdConfig)

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
            "status": str(p.get("status", "UNKNOWN"))[:256], "terminal_connected": bool(p.get("terminal_connected", False)),
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
    telemetry_ready = None
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
        telemetry_ready = heartbeat.status == "TELEMETRY_OK"
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
        telemetry_ready is True,
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
            "telemetry_status",
            "EA telemetry status",
            telemetry_ready,
            "No heartbeat is available." if heartbeat is None else
            ("EA telemetry is healthy." if telemetry_ready else "EA telemetry status is not TELEMETRY_OK."),
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
        "telemetry_ready": telemetry_ready,
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

    # Range diagnostics are derived only from saved Django decisions and
    # broker-reported closed outcomes linked to those decisions.
    range_rows = list(
        StrategyDecisionRecord.objects.filter(symbol=latest_symbol)
        .order_by("-created_at")[:500]
    ) if latest_symbol else []
    range_decision_ids = []
    range_detected = range_approved = range_deferred = range_rejected = 0
    range_evaluated = 0
    latest_range = None
    for row in range_rows:
        features = row.features if isinstance(row.features, dict) else {}
        stages = features.get("stages", {})
        opportunity = stages.get("opportunities", {}) if isinstance(stages, dict) else {}
        if not isinstance(opportunity, dict):
            continue
        checks = opportunity.get("checks", [])
        range_check = next((
            item for item in checks
            if isinstance(item, dict) and str(item.get("name", "")).lower() == "range_edge_fade"
        ), None) if isinstance(checks, list) else None
        if range_check is not None:
            range_evaluated += 1
            check_status = str(range_check.get("status", "")).upper()
            if check_status in {"REJECTED", "BLOCKED", "FAILED"} or range_check.get("passed") is False:
                range_rejected += 1
            elif check_status in {"DEFERRED", "WAIT", "WAITING", "PENDING"}:
                range_deferred += 1
        setup = opportunity.get("setup", {})
        if isinstance(setup, dict) and str(setup.get("type", "")).upper() == "RANGE_EDGE_FADE":
            range_detected += 1
            range_decision_ids.append(row.decision_id)
            if opportunity.get("approved") is True:
                range_approved += 1
            if latest_range is None:
                latest_range = {
                    "decision_id": row.decision_id,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                    "side": setup.get("side"),
                    "range_high": setup.get("range_high"),
                    "range_low": setup.get("range_low"),
                    "range_width_atr": setup.get("range_width_atr"),
                    "boundary_touches": setup.get("boundary_touches"),
                    "status": opportunity.get("status"),
                    "reason": opportunity.get("reason"),
                    "explanation": opportunity.get("explanation"),
                }

    range_outcomes = LearningTradeOutcome.objects.none()
    heartbeat = EAHeartbeat.objects.order_by("-last_seen").first()
    if range_decision_ids and heartbeat is not None and heartbeat.instance_id and heartbeat.account_login:
        range_outcomes = LearningTradeOutcome.objects.filter(
            decision_id__in=range_decision_ids,
            instance_id=heartbeat.instance_id,
            account_login=heartbeat.account_login,
            symbol=latest_symbol,
            closed_at__isnull=False,
        )
    trade_totals = range_outcomes.aggregate(
        closed_trades=Count("id"),
        wins=Count("id", filter=Q(realized_pnl__gt=0)),
        losses=Count("id", filter=Q(realized_pnl__lt=0)),
        net_pnl=Sum("realized_pnl"),
        avg_r=Avg("r_multiple"),
    )
    range_analysis = {
        "symbol": latest_symbol or None,
        "lookback_decisions": len(range_rows),
        "evaluated": range_evaluated,
        "detected": range_detected,
        "approved": range_approved,
        "deferred": range_deferred,
        "rejected": range_rejected,
        "closed_trades": trade_totals["closed_trades"] or 0,
        "winning_trades": trade_totals["wins"] or 0,
        "losing_trades": trade_totals["losses"] or 0,
        "win_rate_percent": round((trade_totals["wins"] or 0) * 100 / trade_totals["closed_trades"], 2) if trade_totals["closed_trades"] else None,
        "net_pnl": str(trade_totals["net_pnl"] or Decimal("0")),
        "average_r_multiple": float(trade_totals["avg_r"]) if trade_totals["avg_r"] is not None else None,
        "latest_setup": latest_range,
        "performance_scope_available": bool(heartbeat and heartbeat.instance_id and heartbeat.account_login),
        "performance_note": "Closed-trade metrics include only broker outcomes linked to RANGE_EDGE_FADE decisions for the latest EA account and instance.",
    }
    return JsonResponse({
        "ok": True,
        "decisions": decisions,
        "range_analysis": range_analysis,
        "execution": _dashboard_execution_state(latest_symbol),
    })


@require_GET
def dashboard_snapshot(request):
    """Return a compact, paste-friendly market/decision snapshot."""
    now = datetime.now(timezone.utc)
    decision = StrategyDecisionRecord.objects.order_by("-created_at").first()
    heartbeat = EAHeartbeat.objects.order_by("-last_seen").first()
    symbol = decision.symbol if decision else (heartbeat.symbol if heartbeat else "")
    features = decision.features if decision and isinstance(decision.features, dict) else {}
    payload = heartbeat.payload if heartbeat and isinstance(heartbeat.payload, dict) else {}

    def compact(value, depth=0):
        """Bound nested diagnostic data so clipboard output stays manageable."""
        if depth >= 5:
            return "…"
        if isinstance(value, dict):
            result = {}
            for index, (key, item) in enumerate(value.items()):
                if index >= 24:
                    result["_omitted_keys"] = len(value) - 24
                    break
                result[str(key)] = compact(item, depth + 1)
            return result
        if isinstance(value, (list, tuple)):
            items = [compact(item, depth + 1) for item in value[:12]]
            if len(value) > 12:
                items.append("… +" + str(len(value) - 12))
            return items
        if isinstance(value, str) and len(value) > 180:
            return value[:177] + "..."
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        return str(value)[:180]

    bars_query = MarketBar.objects.filter(symbol=symbol, timeframe="M15")
    if heartbeat is not None:
        bars_query = bars_query.filter(source=heartbeat.instance_id)
    bars = list(bars_query.order_by("-timestamp")[:20])
    bars.reverse()

    trade_mode = payload.get("account_trade_mode")
    is_demo = (
        isinstance(trade_mode, str) and trade_mode.strip().upper() == "DEMO"
    ) or (isinstance(trade_mode, int) and not isinstance(trade_mode, bool) and trade_mode == 0)
    profile = "demo" if is_demo else "standard"
    from .thresholds import defaults_for
    threshold_values = defaults_for(profile)
    saved_thresholds = DecisionThresholdConfig.objects.filter(profile=profile).first()
    if saved_thresholds and isinstance(saved_thresholds.values, dict):
        for group, updates in saved_thresholds.values.items():
            if group in threshold_values and isinstance(updates, dict):
                threshold_values[group].update({
                    key: value for key, value in updates.items()
                    if key in threshold_values[group]
                })

    verdict_data = None
    if decision is not None:
        verdict = RiskVerdictRecord.objects.filter(
            decision_id=decision.decision_id
        ).order_by("-created_at").first()
        if verdict is not None:
            verdict_data = {
                "approved": verdict.approved,
                "reason": verdict.reason_code,
                "volume": str(verdict.approved_volume),
                "sl": str(verdict.stop_loss) if verdict.stop_loss is not None else None,
                "tp": str(verdict.take_profit) if verdict.take_profit is not None else None,
                "details": compact(verdict.details if isinstance(verdict.details, dict) else {}),
            }

    heartbeat_age = None
    if heartbeat is not None:
        last_seen = heartbeat.last_seen
        if last_seen.tzinfo is None or last_seen.utcoffset() is None:
            last_seen = last_seen.replace(tzinfo=timezone.utc)
        heartbeat_age = max(0, int((now - last_seen).total_seconds()))

    account_payload = payload.get("account", {})
    if not isinstance(account_payload, dict):
        account_payload = {}
    safe_account = {
        key: account_payload[key]
        for key in ("balance", "equity", "margin", "free_margin", "margin_level", "profit", "currency", "leverage")
        if key in account_payload
    }

    latest_decisions = StrategyDecisionRecord.objects.filter(
        symbol=symbol
    ).order_by("-created_at")[:3] if symbol else []
    execution = _dashboard_execution_state(symbol)
    compact_execution = {
        key: execution.get(key)
        for key in ("trading_authorization", "authorization_detail", "command_creation_enabled",
                    "command_execution_enabled", "heartbeat_fresh", "telemetry_ready",
                    "terminal_connected", "terminal_trade_allowed", "demo_account_confirmed",
                    "symbol_matches")
        if key in execution
    }
    compact_execution["checks"] = compact(execution.get("checks", []))

    return JsonResponse({
        "v": 2,
        "at": now.isoformat(timespec="seconds"),
        "market": {
            "symbol": symbol or None,
            "tf": "M15",
            "context": compact(features.get("market_snapshot", {})),
            "quote": compact(payload.get("quote", {})),
            "quote_time": payload.get("quote_time"),
            # Each candle is [UTC time, open, high, low, close, volume].
            "ohlcv": [
                [bar.timestamp.isoformat(timespec="seconds"), str(bar.open), str(bar.high),
                 str(bar.low), str(bar.close), str(bar.volume)]
                for bar in bars
            ],
            "candles_included": len(bars),
        },
        "decision": None if decision is None else {
            "id": decision.decision_id,
            "side": decision.action,
            "confidence": decision.confidence,
            "reason": decision.rationale_code,
            "model": decision.model_version,
            "time": decision.created_at.isoformat(timespec="seconds") if decision.created_at else None,
            "evidence": compact(features),
        },
        "recent": [
            [row.created_at.isoformat(timespec="seconds") if row.created_at else None,
             row.action, row.rationale_code, row.confidence]
            for row in latest_decisions
        ],
        "risk": verdict_data,
        "thresholds": {"profile": profile, "values": compact(threshold_values)},
        "broker": {
            "ea": heartbeat.ea_version if heartbeat else None,
            "status": heartbeat.status if heartbeat else None,
            "heartbeat_age_s": heartbeat_age,
            "server": heartbeat.server_name if heartbeat else None,
            "trade_mode": trade_mode,
            "account": safe_account,
            "symbol_spec": compact(payload.get("symbol_info", {})),
            "positions": compact(payload.get("positions", [])[:5] if isinstance(payload.get("positions"), list) else []),
        },
        "execution": compact_execution,
        "data": {
            "decision": decision is not None,
            "heartbeat": heartbeat is not None,
            "candles_available": bool(bars),
            "candles_stored_for_source": MarketBar.objects.filter(
                symbol=symbol, timeframe="M15",
                **({"source": heartbeat.instance_id} if heartbeat is not None else {})
            ).count() if symbol else 0,
        },
    })

def _active_threshold_profile():
    """Use broker telemetry to select demo vs standard thresholds; never trust the browser."""
    heartbeat = EAHeartbeat.objects.order_by("-last_seen").first()
    payload = heartbeat.payload if heartbeat and isinstance(heartbeat.payload, dict) else {}
    trade_mode = payload.get("account_trade_mode")
    is_demo = (
        isinstance(trade_mode, str) and trade_mode.strip().upper() == "DEMO"
    ) or (isinstance(trade_mode, int) and not isinstance(trade_mode, bool) and trade_mode == 0)
    return "demo" if is_demo else "standard"


@require_http_methods(["GET", "POST"])
def decision_thresholds(request):
    """Read or update the active profile's editable Django strategy thresholds."""
    from .thresholds import public_schema, validate_values

    profile = _active_threshold_profile()
    record, _ = DecisionThresholdConfig.objects.get_or_create(profile=profile)
    if request.method == "GET":
        return JsonResponse({"ok": True, **public_schema(profile, record.values)})
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "GET or POST required"}, status=405)
    try:
        payload = _body(request)
        if payload.get("action") == "reset":
            record.values = {}
            record.save(update_fields=["values", "updated_at"])
            return JsonResponse({"ok": True, "reset": True, **public_schema(profile, {})})
        validated = validate_values(payload.get("values"), profile)
        # Store the complete validated active profile, so a subsequent restart/worker
        # cannot silently revert to constructor defaults.
        record.values = validated
        record.save(update_fields=["values", "updated_at"])
        return JsonResponse({"ok": True, **public_schema(profile, record.values)})
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)

@require_GET
def dashboard_pnl_summary(request):
    """Return realized P&L for the selected inclusive UTC date interval.

    Results are scoped to the latest EA heartbeat's account and instance so
    unrelated accounts or historical terminals are never mixed together.
    """
    start_raw = request.GET.get("from", "")
    end_raw = request.GET.get("to", "")
    try:
        start_date = date.fromisoformat(start_raw)
        end_date = date.fromisoformat(end_raw)
    except (TypeError, ValueError):
        return JsonResponse(
            {"ok": False, "error": "from and to must be valid YYYY-MM-DD dates"},
            status=400,
        )
    if start_date.isoformat() != start_raw or end_date.isoformat() != end_raw:
        return JsonResponse({"ok": False, "error": "Dates must use YYYY-MM-DD format"}, status=400)
    if end_date < start_date:
        return JsonResponse({"ok": False, "error": "'to' date must be on or after 'from' date"}, status=400)
    if (end_date - start_date).days > 3660:
        return JsonResponse({"ok": False, "error": "Date interval cannot exceed 10 years"}, status=400)

    start_at = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
    end_at = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=timezone.utc)

    heartbeat = EAHeartbeat.objects.order_by("-last_seen").first()
    if heartbeat is None or not heartbeat.account_login or not heartbeat.instance_id:
        return JsonResponse({
            "ok": True,
            "from": start_date.isoformat(),
            "to": end_date.isoformat(),
            "currency": None,
            "account_login": None,
            "server_name": None,
            "total_profit": "0",
            "total_loss": "0",
            "net_pnl": "0",
            "closed_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "break_even_trades": 0,
            "scope_available": False,
            "note": "No active EA account scope is available. Connect the intended MT5 account to view its results.",
        })

    outcomes = LearningTradeOutcome.objects.filter(
        account_login=heartbeat.account_login,
        instance_id=heartbeat.instance_id,
        closed_at__gte=start_at,
        closed_at__lt=end_at,
    )
    totals = outcomes.aggregate(
        total_profit=Sum("realized_pnl", filter=Q(realized_pnl__gt=0)),
        total_loss=Sum("realized_pnl", filter=Q(realized_pnl__lt=0)),
        net_pnl=Sum("realized_pnl"),
        closed_trades=Count("id"),
        winning_trades=Count("id", filter=Q(realized_pnl__gt=0)),
        losing_trades=Count("id", filter=Q(realized_pnl__lt=0)),
        break_even_trades=Count("id", filter=Q(realized_pnl=0)),
    )
    total_profit = totals["total_profit"] or Decimal("0")
    total_loss = abs(totals["total_loss"] or Decimal("0"))
    net_pnl = totals["net_pnl"] or Decimal("0")
    account_payload = heartbeat.payload if isinstance(heartbeat.payload, dict) else {}
    account = account_payload.get("account", {})
    currency = account.get("currency") if isinstance(account, dict) else None

    return JsonResponse({
        "ok": True,
        "from": start_date.isoformat(),
        "to": end_date.isoformat(),
        "currency": currency,
        "account_login": heartbeat.account_login,
        "server_name": heartbeat.server_name or account_payload.get("server_name"),
        "instance_id": heartbeat.instance_id,
        "total_profit": str(total_profit),
        "total_loss": str(total_loss),
        "net_pnl": str(net_pnl),
        "closed_trades": totals["closed_trades"] or 0,
        "winning_trades": totals["winning_trades"] or 0,
        "losing_trades": totals["losing_trades"] or 0,
        "break_even_trades": totals["break_even_trades"] or 0,
        "scope_available": True,
        "pnl_basis": "LearningTradeOutcome.realized_pnl; values must be broker-net of commission, swap and spread.",
    })
