"""Load completed context from the same authenticated EA's candle history."""
from datetime import datetime, timedelta, timezone

from .models import MarketBar


def load_decision_context(*, source: str, symbol: str, decision_time: float) -> dict[str, list[dict]]:
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
    return context
