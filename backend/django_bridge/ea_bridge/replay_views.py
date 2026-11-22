"""Read-only historical candle replay. Never calls strategy execution or EA commands."""
from datetime import datetime, timezone
from django.http import JsonResponse
from django.db.models import Count, Sum, Q
from .models import LearningTradeOutcome
from django.shortcuts import render
from django.views.decorators.http import require_GET
from django.views.decorators.csrf import ensure_csrf_cookie
from .models import MarketBar

ALLOWED_TIMEFRAMES = {"M1", "M5", "M15", "H1"}
MAX_BARS = 2000

@ensure_csrf_cookie
@require_GET
def simulator(request):
    return render(request, "ea_bridge/historical_simulator.html")

@require_GET
def simulator_bars(request):
    symbol = (request.GET.get("symbol") or "").strip()
    timeframe = (request.GET.get("timeframe") or "M5").upper()
    source = (request.GET.get("source") or "").strip()
    if not symbol or len(symbol) > 32 or timeframe not in ALLOWED_TIMEFRAMES:
        return JsonResponse({"error": "Valid symbol and timeframe required"}, status=400)
    if source and len(source) > 128:
        return JsonResponse({"error": "Invalid source"}, status=400)
    try:
        limit = int(request.GET.get("limit", "700"))
        if not 1 <= limit <= MAX_BARS:
            raise ValueError()
    except (ValueError, TypeError):
        return JsonResponse({"error": "limit must be between 1 and 2000"}, status=400)
    before = request.GET.get("before")
    start = request.GET.get("start")
    qs = MarketBar.objects.filter(symbol=symbol, timeframe=timeframe)
    if source:
        qs = qs.filter(source=source)
    else:
        # One consistent source is essential; otherwise overlapping broker
        # feeds could produce duplicate timestamps or inconsistent prices.
        latest_source = qs.order_by("-timestamp").values_list("source", flat=True).first()
        if not latest_source:
            return JsonResponse({"symbol": symbol, "timeframe": timeframe, "source": None, "bars": []})
        source = latest_source
        qs = qs.filter(source=source)
    if before:
        try:
            parsed = datetime.fromisoformat(before.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError()
        except ValueError:
            return JsonResponse({"error": "before must be a timezone-aware ISO 8601 timestamp"}, status=400)
        qs = qs.filter(timestamp__lte=parsed)
    if start:
        try:
            start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
            if start_dt.tzinfo is None:
                raise ValueError()
        except ValueError:
            return JsonResponse({"error": "start must be timezone-aware ISO 8601"}, status=400)
        qs = qs.filter(timestamp__gte=start_dt)
    # The chart is a bounded window, NOT the full history used by backtesting.
    # In a selected date range, start at the first candle so replay starts at
    # the chosen date. Outside that mode, show the latest candles as before.
    fields = ("timestamp", "open", "high", "low", "close", "volume")
    if start:
        sampled = list(qs.order_by("timestamp").values(*fields)[:limit + 1])
        truncated = len(sampled) > limit
        rows = sampled[:limit]
    else:
        sampled = list(qs.order_by("-timestamp").values(*fields)[:limit + 1])
        truncated = len(sampled) > limit
        rows = sampled[:limit]
        rows.reverse()
    return JsonResponse({
        "symbol": symbol, "timeframe": timeframe, "source": source,
        "truncated": truncated,
        "window": "earliest_in_range" if start else "latest",
        "bars": [
            {"time": int(row["timestamp"].timestamp()), "open": float(row["open"]),
             "high": float(row["high"]), "low": float(row["low"]),
             "close": float(row["close"]), "volume": float(row["volume"])}
            for row in rows
        ],
    })

@require_GET
def simulator_recorded_stats(request):
    """Historical closed broker outcomes only; NOT hypothetical replay/backtest trades.

    Each request is bounded by the replay cursor and the loaded chart's start time.
    No EA command, strategy evaluation, or simulated trade is created.
    """
    symbol = (request.GET.get("symbol") or "").strip()
    if not symbol or len(symbol) > 32:
        return JsonResponse({"error": "Valid symbol required"}, status=400)
    try:
        start = datetime.fromisoformat(request.GET["start"].replace("Z", "+00:00"))
        through = datetime.fromisoformat(request.GET["through"].replace("Z", "+00:00"))
        if start.tzinfo is None or through.tzinfo is None or through < start:
            raise ValueError()
    except (KeyError, ValueError):
        return JsonResponse({"error": "Valid timezone-aware start and through required"}, status=400)
    # Include only outcomes with a genuine broker close timestamp, never receipt time.
    records = LearningTradeOutcome.objects.filter(
        symbol=symbol, closed_at__gte=start, closed_at__lte=through
    )
    totals = records.aggregate(
        total=Count("id"),
        wins=Count("id", filter=Q(realized_pnl__gt=0)),
        losses=Count("id", filter=Q(realized_pnl__lt=0)),
        net_pnl=Sum("realized_pnl"),
    )
    total = totals["total"] or 0
    accounts = list(records.values_list("account_login", flat=True).distinct()[:2])
    mixed_accounts = len(accounts) > 1
    return JsonResponse({
        "scope": "recorded_closed_trades_not_replay_backtest",
        "symbol": symbol, "trade_count": total,
        "wins": totals["wins"], "losses": totals["losses"],
        "win_rate_percent": round(100 * totals["wins"] / total, 2) if total else None,
        "total_gain": str(totals["net_pnl"]) if total and not mixed_accounts else None,
        "mixed_accounts": mixed_accounts,
        "account_login": accounts[0] if len(accounts) == 1 else None,
        "currency": None,
        "start": start.isoformat(), "through": through.isoformat(),
    })
