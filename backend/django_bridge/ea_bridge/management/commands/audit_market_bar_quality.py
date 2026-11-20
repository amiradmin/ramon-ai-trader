"""Read-only, source-separated inventory of stored candle quality."""
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone

from django.core.management.base import BaseCommand, CommandError
from ea_bridge.models import MarketBar

PERIODS = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400}


def inspect_rows(rows):
    groups = {}
    for row in rows:
        ts = row.timestamp.astimezone(timezone.utc)
        key = (row.source, row.symbol, row.timeframe, ts.year)
        if key not in groups:
            groups[key] = {"source": row.source, "symbol": row.symbol, "timeframe": row.timeframe,
                "year": ts.year, "rows": 0, "aligned_rows": 0, "off_grid_rows": 0,
                "invalid_ohlcv_rows": 0, "duplicate_timestamps": 0, "gap_intervals": 0,
                "elapsed_grid_slots_without_bar": 0, "max_gap_seconds": 0,
                "first": ts.isoformat(), "last": ts.isoformat(), "offset_seconds_counts": Counter(),
                "_previous": None, "_previous_any": None, "_hash": hashlib.sha256()}
        item = groups[key]
        item["rows"] += 1
        item["last"] = ts.isoformat()
        if item["_previous_any"] == ts:
            item["duplicate_timestamps"] += 1
        item["_previous_any"] = ts
        period = PERIODS.get(row.timeframe)
        offset = ts.timestamp() % period if period else None
        aligned = period is not None and offset == 0
        if aligned:
            item["aligned_rows"] += 1
            prev = item["_previous"]
            if prev is not None:
                delta = (ts - prev).total_seconds()
                if delta > period:
                    item["gap_intervals"] += 1
                    item["elapsed_grid_slots_without_bar"] += int(delta // period) - 1
                    item["max_gap_seconds"] = max(item["max_gap_seconds"], delta)
            item["_previous"] = ts
        elif period:
            item["off_grid_rows"] += 1
            item["offset_seconds_counts"][str(offset)] += 1
        values = (row.open, row.high, row.low, row.close, row.volume)
        o, h, l, c, v = values
        if (not all(x.is_finite() for x in values) or min(o, h, l, c) <= 0 or v < 0
                or h < max(o, l, c) or l > min(o, h, c)):
            item["invalid_ohlcv_rows"] += 1
        item["_hash"].update((json.dumps([ts.isoformat(), *map(str, values)], separators=(",", ":")) + "\n").encode())
    result = []
    for item in groups.values():
        item["sha256"] = item.pop("_hash").hexdigest()
        item.pop("_previous")
        item.pop("_previous_any")
        item["offset_seconds_counts"] = dict(item["offset_seconds_counts"])
        item["status"] = ("UNKNOWN_TIMEFRAME" if item["timeframe"] not in PERIODS else
                          "INVALID" if item["off_grid_rows"] or item["invalid_ohlcv_rows"] or item["duplicate_timestamps"] else "REQUIRES_COVERAGE_REVIEW")
        result.append(item)
    return result


class Command(BaseCommand):
    help = "Report stored candle integrity by source, symbol, timeframe and UTC year; no database writes."

    def add_arguments(self, parser):
        parser.add_argument("--source")
        parser.add_argument("--symbol")
        parser.add_argument("--first-year", type=int)
        parser.add_argument("--last-year", type=int)

    def handle(self, *args, **opts):
        first, last = opts["first_year"], opts["last_year"]
        if (first is not None and not 1 <= first <= 9998) or (last is not None and not 1 <= last <= 9998) or (first and last and first > last):
            raise CommandError("Invalid year range")
        query = MarketBar.objects.all()
        if opts["source"]:
            query = query.filter(source=opts["source"])
        if opts["symbol"]:
            query = query.filter(symbol=opts["symbol"])
        if first:
            query = query.filter(timestamp__gte=datetime(first, 1, 1, tzinfo=timezone.utc))
        if last:
            query = query.filter(timestamp__lt=datetime(last + 1, 1, 1, tzinfo=timezone.utc))
        rows = query.order_by("source", "symbol", "timeframe", "timestamp", "pk").iterator(chunk_size=5000)
        report = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), "groups": inspect_rows(rows),
            "notes": ["Absent groups contain no rows; alignment does not establish coverage or correctness.",
                      "Elapsed grid slots include market closures and are not verified missing trading candles.",
                      "Hashes identify stored OHLCV rows, not original broker exports or import provenance.",
                      "Uniqueness is enforced per source, symbol, timeframe and timestamp by the database."]}
        self.stdout.write(json.dumps(report, indent=2))
