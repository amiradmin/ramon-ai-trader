"""Normalize OHLCV records to finite decimals and timezone-aware UTC."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping
from .schemas import PipelineError

def normalize_bar(payload: Mapping[str, Any]) -> dict[str, Any]:
    try:
        stamp = datetime.fromisoformat(str(payload["timestamp"]).replace("Z", "+00:00"))
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise PipelineError("timestamp must include a timezone")
        row = {"symbol": str(payload["symbol"]).strip(), "timeframe": str(payload["timeframe"]).strip().upper(),
               "timestamp": stamp.astimezone(timezone.utc),
               **{k: Decimal(str(payload.get(k, 0 if k == "volume" else None))) for k in ("open","high","low","close","volume")}}
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        raise PipelineError("invalid OHLCV bar") from exc
    if not row["symbol"] or not row["timeframe"]:
        raise PipelineError("symbol and timeframe are required")
    values = [row[k] for k in ("open","high","low","close","volume")]
    if any(not v.is_finite() for v in values):
        raise PipelineError("OHLCV values must be finite")
    if row["high"] < max(row["open"], row["close"], row["low"]) or row["low"] > min(row["open"], row["close"], row["high"]):
        raise PipelineError("bar high/low are inconsistent")
    if row["volume"] < 0:
        raise PipelineError("volume cannot be negative")
    return row
