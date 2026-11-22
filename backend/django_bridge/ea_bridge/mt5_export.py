"""Validate a LiteFinance MT5 M1 export and derive UTC research candles.

MT5's CSV uses broker wall time. The offset rule below is a provisional
LiteFinance +2/+3 assumption; verify against broker ticks before research use.
"""
import calendar
import csv
import hashlib
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

HEADER = ["<DATE>", "<TIME>", "<OPEN>", "<HIGH>", "<LOW>", "<CLOSE>",
          "<TICKVOL>", "<VOL>", "<SPREAD>"]


def last_sunday(year, month):
    return max(week[calendar.SUNDAY] for week in calendar.monthcalendar(year, month))


def server_to_utc(stamp):
    transition_march = datetime(stamp.year, 3, last_sunday(stamp.year, 3)).date()
    transition_october = datetime(stamp.year, 10, last_sunday(stamp.year, 10)).date()
    if stamp.date() in (transition_march, transition_october):
        raise ValueError("DST transition Sunday needs an independently verified offset")
    hours = 3 if transition_march < stamp.date() < transition_october else 2
    return (stamp - timedelta(hours=hours)).replace(tzinfo=timezone.utc)


def read_m1(path):
    """Return complete, strictly increasing bars; reject ambiguous or corrupt files."""
    path = Path(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    bars = []
    gaps = Counter()
    previous_server = None
    previous_utc = None
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream, delimiter="\t")
        if next(reader, None) != HEADER:
            raise ValueError("Expected MT5 tab-separated M1 export header")
        for line, row in enumerate(reader, 2):
            if len(row) != len(HEADER):
                raise ValueError(f"Line {line}: expected {len(HEADER)} columns")
            try:
                stamp = datetime.strptime(row[0] + " " + row[1], "%Y.%m.%d %H:%M:%S")
                opening, high, low, closing, tickvol, volume, spread = map(Decimal, row[2:])
                utc = server_to_utc(stamp)
                if (stamp.second or stamp.microsecond or
                        not all(v.is_finite() for v in (opening, high, low, closing, tickvol, volume, spread)) or
                        min(opening, high, low, closing) <= 0 or
                        high < max(opening, low, closing) or low > min(opening, high, closing) or
                        tickvol < 0 or volume < 0 or spread < 0 or
                        tickvol != int(tickvol) or spread != int(spread)):
                    raise ValueError("invalid M1 candle")
                if previous_server is not None and stamp <= previous_server:
                    raise ValueError("duplicate or decreasing broker time")
                if previous_utc is not None and utc <= previous_utc:
                    raise ValueError("duplicate or decreasing UTC time")
            except (ValueError, InvalidOperation, OverflowError) as exc:
                raise ValueError(f"Line {line}: {exc}") from exc
            if previous_utc is not None:
                minutes = int((utc - previous_utc).total_seconds() // 60)
                if minutes > 1:
                    gaps[minutes] += 1
            bars.append((utc, opening, high, low, closing, tickvol))
            previous_server, previous_utc = stamp, utc
    if not bars:
        raise ValueError("Empty MT5 export")
    return bars, {"sha256": digest, "rows": len(bars), "first": bars[0][0].isoformat(),
                  "last": bars[-1][0].isoformat(), "gap_lengths_minutes": dict(sorted(gaps.items()))}


def aggregate_m15(bars):
    """Require all 15 consecutive minute opens for each UTC aligned M15 bar."""
    groups = {}
    for bar in bars:
        start = bar[0].replace(minute=(bar[0].minute // 15) * 15)
        groups.setdefault(start, []).append(bar)
    complete = []
    incomplete = 0
    for start, minute_bars in sorted(groups.items()):
        if [bar[0] for bar in minute_bars] != [start + timedelta(minutes=n) for n in range(15)]:
            incomplete += 1
            continue
        complete.append((start, minute_bars[0][1], max(bar[2] for bar in minute_bars),
                         min(bar[3] for bar in minute_bars), minute_bars[-1][4],
                         sum((bar[5] for bar in minute_bars), Decimal(0))))
    return complete, incomplete
