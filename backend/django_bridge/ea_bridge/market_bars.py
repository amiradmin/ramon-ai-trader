"""Shared validation for market-bar timestamps before persistence."""
from datetime import timezone as datetime_timezone


def validate_bar_timestamp(timestamp, timeframe):
    """Require candle-open alignment for supported fixed intraday timeframes."""
    normalized_timeframe = str(timeframe).strip().upper()
    utc_timestamp = timestamp.astimezone(datetime_timezone.utc)
    interval_minutes = {"M1": 1, "M5": 5, "M15": 15, "H1": 60}.get(normalized_timeframe)
    if interval_minutes is not None:
        aligned = (
            utc_timestamp.second == 0
            and utc_timestamp.microsecond == 0
            and utc_timestamp.minute % interval_minutes == 0
        )
        if normalized_timeframe == "H1":
            aligned = aligned and utc_timestamp.minute == 0
        if not aligned:
            label = "hour" if normalized_timeframe == "H1" else f"{interval_minutes}-minute"
            raise ValueError(
                f"{normalized_timeframe} bar timestamp must be aligned to a UTC {label} boundary"
            )
    return timestamp
