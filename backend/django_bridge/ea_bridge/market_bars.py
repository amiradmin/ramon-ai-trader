"""Shared validation for market-bar timestamps before persistence."""
from datetime import timezone as datetime_timezone


def validate_bar_timestamp(timestamp, timeframe):
    """Require candle-open alignment for supported fixed intraday timeframes."""
    normalized_timeframe = str(timeframe).strip().upper()
    if normalized_timeframe == "M15":
        utc_timestamp = timestamp.astimezone(datetime_timezone.utc)
        if (
            utc_timestamp.second != 0
            or utc_timestamp.microsecond != 0
            or utc_timestamp.minute % 15 != 0
        ):
            raise ValueError(
                "M15 bar timestamp must be aligned to a UTC 15-minute boundary"
            )
    return timestamp
