"""Load completed context from the same authenticated EA's candle history."""
from datetime import datetime, timedelta, timezone

from .models import MarketBar
from ramon_pipeline.signalflow.trade_planning import previous_week_bounds


def load_decision_context(*, source: str, symbol: str, decision_time: float, include_previous_week: bool = False) -> dict[str, list[dict]]:
    as_of = datetime.fromtimestamp(decision_time, tz=timezone.utc)
    context = {}
    for timeframe, seconds in (("H1", 3600), ("M5", 300)):
        rows = list(MarketBar.objects.filter(
            source=source, symbol=symbol, timeframe=timeframe,
            timestamp__lte=as_of - timedelta(seconds=seconds),
            received_at__lte=as_of,
        ).order_by("-timestamp")[:120])
        context[timeframe] = [
            {"time": row.timestamp.timestamp(), "open": float(row.open),
             "high": float(row.high), "low": float(row.low), "close": float(row.close), "closed": True}
            for row in reversed(rows)
        ]
    if include_previous_week:
        start, end = previous_week_bounds(decision_time)
        rows = MarketBar.objects.filter(source=source, symbol=symbol, timeframe="M15",
            timestamp__gte=datetime.fromtimestamp(start, timezone.utc),
            timestamp__lt=datetime.fromtimestamp(end, timezone.utc),
            timestamp__lte=as_of-timedelta(seconds=900), received_at__lte=as_of).order_by("timestamp")
        context["WEEK_M15"] = [
            {"time": row.timestamp.timestamp(), "open": float(row.open), "high": float(row.high),
             "low": float(row.low), "close": float(row.close), "closed": True}
            for row in rows
        ]
    return context
