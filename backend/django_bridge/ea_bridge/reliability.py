"""Read-only reconciliation: never authorizes, retries or changes broker commands."""
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET

from .models import EACommand, EAHeartbeat, LearningTradeOutcome, MarketBar, ReliabilityIncident, DecisionEvaluation


def record_telemetry_gap(previous, account_login, server_name, quote_time=None):
    # Called inside telemetry's transaction and heartbeat lock. An outage cannot
    # be observed while the host is off; record its evidence on first recovery.
    if previous and previous.account_login == account_login and previous.server_name == server_name:
        seconds = (timezone.now() - previous.last_seen).total_seconds()
        if seconds > 90:
            ReliabilityIncident.objects.create(
                instance_id=previous.instance_id, account_login=account_login, server_name=server_name,
                kind="TELEMETRY_GAP", started_at=previous.last_seen,
                details={"seconds": round(seconds), "meaning": "Gap between accepted telemetry; cause unknown"},
            )


        if quote_time is not None:
            def stale(raw, received):
                try:
                    parsed = parse_datetime(str(raw))
                    age = (received - parsed).total_seconds() if parsed and timezone.is_aware(parsed) else None
                    return age is None or age < -5 or age > 60
                except (ValueError, TypeError):
                    return True
            before = stale(previous.payload.get("quote_time"), previous.last_seen)
            after = stale(quote_time, timezone.now())
            if before != after:
                ReliabilityIncident.objects.create(
                    instance_id=previous.instance_id, account_login=account_login, server_name=server_name,
                    kind="QUOTE_STALE" if after else "QUOTE_RECOVERED", started_at=timezone.now(),
                    details={"quote_time": str(quote_time), "meaning": "Quote freshness transition at receipt"},
                )


def numeric(value):
    try:
        if isinstance(value, bool):
            return None
        n = Decimal(str(value))
        return n if n.is_finite() else None
    except (ValueError, TypeError, InvalidOperation):
        return None


def reconcile(command, feed, now):
    """Return current evidence, not a replacement for the command's ack state."""
    base = {"command_id": command.command_id, "action": command.action,
            "ack_state": command.state, "evaluation_id": command.evaluation_id}
    def result(state):
        return {**base, "verification": state}
    if (command.instance_id != feed.instance_id or command.account_login != feed.account_login
            or command.payload.get("server_name") != feed.server_name):
        return result("SCOPE_MISMATCH")
    if command.state == "PENDING" and command.expires_at < now:
        return result("PENDING_PAST_EXPIRY")
    if command.state != "EXECUTED":
        return result("REQUIRES_REVIEW" if command.state in {
            "FAILED", "RECONCILIATION_REQUIRED", "EXECUTING", "CLAIMED", "RECEIVED"
        } else "NOT_EXECUTED")
    # Closed outcomes establish OPEN lifecycle even after a position disappears.
    if command.action == "OPEN":
        closed = LearningTradeOutcome.objects.filter(
            instance_id=feed.instance_id, account_login=feed.account_login, symbol=command.symbol,
            trade_id__startswith=f"{feed.account_login}:{feed.server_name}:",
            tags__command_id=command.command_id, closed_at__isnull=False,
        ).exists()
        if closed:
            return result("CONFIRMED_CLOSED_OUTCOME")
    age = (now - feed.last_seen).total_seconds()
    if age < 0 or age > 90 or not feed.terminal_connected:
        return result("TELEMETRY_STALE")
    quote_time = parse_datetime(str(feed.payload.get("quote_time", "")))
    if quote_time is None or not timezone.is_aware(quote_time) or not -5 <= (now-quote_time).total_seconds() <= 60:
        return result("QUOTE_STALE")
    if command.completed_at is None or feed.last_seen <= command.completed_at:
        return result("AWAITING_LATER_TELEMETRY")
    positions = feed.payload.get("positions")
    if not isinstance(positions, list):
        return result("POSITIONS_UNAVAILABLE")
    ticket = str(command.payload.get("position_ticket", "") if command.action != "OPEN"
                 else command.result.get("broker_ticket", ""))
    if not ticket or ticket == "0":
        return result("TICKET_UNVERIFIED")
    position = next((p for p in positions if isinstance(p, dict) and
                     str(p.get("ticket")) == ticket and p.get("symbol") == command.symbol), None)
    if command.action == "CLOSE":
        return result("POSITION_ABSENT_CLOSE_UNPROVEN" if position is None else "POSITION_STILL_OPEN")
    if position is None:
        return result("POSITION_NOT_OBSERVED")
    if command.action not in {"OPEN", "MODIFY_SLTP"}:
        return result("UNSUPPORTED_ACTION")
    tick = numeric(feed.payload.get("symbol_info", {}).get("tick_size"))
    if tick is None or tick <= 0:
        return result("TICK_SIZE_UNAVAILABLE")
    for wanted, observed in [("stop_loss", "sl"), ("take_profit", "tp")]:
        a, b = numeric(command.payload.get(wanted)), numeric(position.get(observed))
        if a is None or b is None:
            return result("POSITION_VALUES_UNAVAILABLE")
        if abs(a - b) > tick / 2:
            return result("POSITION_MISMATCH")
    if command.action == "OPEN":
        volume = numeric(command.payload.get("volume"))
        if volume is None or numeric(position.get("volume")) != volume or position.get("type") != (
                0 if command.payload.get("side") == "BUY" else 1):
            return result("POSITION_MISMATCH")
    return result("CONFIRMED_CURRENT_POSITION")


@require_GET
def reliability_status(request):
    now = timezone.now()
    feeds = EAHeartbeat.objects.filter(status="TELEMETRY_OK").order_by("-last_seen")[:20]
    reports = []
    for feed in feeds:
        payload = feed.payload if isinstance(feed.payload, dict) else {}
        quote_time = parse_datetime(str(payload.get("quote_time", "")))
        quote_age = (now - quote_time).total_seconds() if quote_time and timezone.is_aware(quote_time) else None
        heartbeat_age = (now - feed.last_seen).total_seconds()
        state = "READY"
        if heartbeat_age < 0 or heartbeat_age > 90:
            state = "TELEMETRY_STALE"
        elif not feed.terminal_connected:
            state = "DISCONNECTED"
        elif quote_age is None or quote_age < -5 or quote_age > 60:
            state = "QUOTE_STALE"
        bars = {}
        for tf, seconds in {"M1": 60, "M5": 300, "M15": 900, "H1": 3600}.items():
            bar = MarketBar.objects.filter(source=feed.instance_id, symbol=feed.symbol, timeframe=tf,
                timestamp__lte=now-timedelta(seconds=seconds)).order_by("-timestamp").first()
            close_age = (now - bar.timestamp).total_seconds()-seconds if bar else None
            bars[tf] = {"close_age_seconds": close_age,
                        "fresh": close_age is not None and 0 <= close_age <= seconds + 120}
        if state == "READY" and not bars["M15"]["fresh"]:
            state = "M15_STALE"
        commands = EACommand.objects.filter(instance_id=feed.instance_id, account_login=feed.account_login,
            symbol=feed.symbol, payload__server_name=feed.server_name).select_related("evaluation").order_by("-created_at")[:30]
        incidents = ReliabilityIncident.objects.filter(instance_id=feed.instance_id,
            account_login=feed.account_login, server_name=feed.server_name).order_by("-detected_at")[:10]
        reports.append({"source": feed.instance_id, "symbol": feed.symbol, "state": state,
            "heartbeat_age_seconds": heartbeat_age, "quote_age_seconds": quote_age, "bars": bars,
            "commands": [reconcile(c, feed, now) for c in commands],
            "incidents": list(incidents.values("kind", "started_at", "detected_at", "details")),
            "evaluations": list(DecisionEvaluation.objects.filter(instance_id=feed.instance_id,
                account_login=feed.account_login, server_name=feed.server_name, symbol=feed.symbol)
                .order_by("-created_at").values("id", "created_at", "action", "rationale_code",
                    "verdict__approved", "verdict__reason_code")[:10])})
    response = JsonResponse({"observed_at": now, "affects_orders": False, "feeds": reports})
    response["Cache-Control"] = "no-store"
    return response
