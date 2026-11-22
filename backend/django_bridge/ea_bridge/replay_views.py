"""Read-only historical candle replay. Never calls strategy execution or EA commands."""
from datetime import datetime, timezone
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET
from .models import MarketBar

ALLOWED_TIMEFRAMES = {"M1", "M5", "M15", "H1"}
MAX_BARS = 2000

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
    rows = list(qs.order_by("-timestamp").values("timestamp", "open", "high", "low", "close", "volume")[:limit])
    rows.reverse()
    return JsonResponse({
        "symbol": symbol, "timeframe": timeframe, "source": source,
        "bars": [
            {"time": int(row["timestamp"].timestamp()), "open": float(row["open"]),
             "high": float(row["high"]), "low": float(row["low"]),
             "close": float(row["close"]), "volume": float(row["volume"])}
            for row in rows
        ],
    })
