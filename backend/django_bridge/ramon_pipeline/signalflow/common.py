"""Shared vocabulary and input helpers for the four SignalFlow stages.

Keeping these in one place means every stage speaks the same status language and parses
numbers and timestamps the same way, and no stage has to import another stage's privates.
"""
import math
from datetime import datetime, timezone
from typing import Any

# Decision status shared by every stage's result dict.
ACCEPTED = "ACCEPTED"  # the stage approves; later stages may evaluate further
REJECTED = "REJECTED"  # invalid or unsuitable; waiting will not help
DEFERRED = "DEFERRED"  # valid so far but not ready; re-evaluate on a later bar/tick


class InvalidMarketData(ValueError):
    """Input that cannot be analysed. ``reason`` is a stable code, ``detail`` is human readable."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def finite_number(value: Any, name: str) -> float:
    """Convert to a finite float; booleans, non-numbers, NaN and infinity are rejected."""
    if isinstance(value, bool):
        raise InvalidMarketData("INVALID_MARKET_DATA", f"{name} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise InvalidMarketData("INVALID_MARKET_DATA", f"{name} must be numeric") from None
    if not math.isfinite(number):
        raise InvalidMarketData("INVALID_MARKET_DATA", f"{name} must be finite")
    return number


def parse_timestamp(value: Any) -> float:
    """Epoch seconds, a datetime or an ISO-8601 string -> epoch seconds. Naive times are taken as UTC."""
    if isinstance(value, datetime):
        parsed = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    if isinstance(value, str):
        text = value.strip()
        try:
            return finite_number(text, "time")
        except InvalidMarketData:
            pass
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            raise InvalidMarketData("INVALID_MARKET_DATA", "time is not a valid timestamp") from None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    return finite_number(value, "time")


def clip(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))
