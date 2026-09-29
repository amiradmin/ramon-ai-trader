"""Clock features known at forecast time, without future market prices."""
from __future__ import annotations

from datetime import datetime, timezone
from math import cos, pi, sin
from typing import Sequence
from zoneinfo import ZoneInfo


TEHRAN = ZoneInfo("Asia/Tehran")
M15_SECONDS = 900


def tehran_hour(timestamp: int) -> float:
    local = datetime.fromtimestamp(timestamp, timezone.utc).astimezone(TEHRAN)
    return local.hour + local.minute / 60


def hour_covariates(timestamps: Sequence[int]) -> dict[str, list[float]]:
    """A continuous cyclic representation of Tehran local clock time."""
    phase = [2 * pi * tehran_hour(int(timestamp)) / 24 for timestamp in timestamps]
    return {
        "hour_sin": [sin(value) for value in phase],
        "hour_cos": [cos(value) for value in phase],
    }


def forecast_clock_inputs(timestamps: Sequence[int], horizon: int) -> tuple[dict, dict]:
    if not timestamps or horizon < 1:
        raise ValueError("timestamps and positive horizon required")
    if any(b <= a for a, b in zip(timestamps, timestamps[1:])):
        raise ValueError("timestamps must increase")
    future = [int(timestamps[-1]) + M15_SECONDS * step for step in range(1, horizon + 1)]
    return hour_covariates(timestamps), hour_covariates(future)
