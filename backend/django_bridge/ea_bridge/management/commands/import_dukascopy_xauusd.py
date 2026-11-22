"""Download Dukascopy XAUUSD BID M1 data and import complete M15 bars.

This is a research-only, explicitly named market source. It never touches the
broker telemetry rows, live order execution, or account specifications.
"""
import hashlib
import lzma
import math
import struct
import time as clock
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ea_bridge.models import MarketBar

SOURCE = "dukascopy-xauusd-bid-m15"
SYMBOL = "XAUUSD"
SCALE = Decimal("1000")
RECORD = struct.Struct(">IIIIIf")
MINUTE = 60
QUARTER = 900
BASE_URL = "https://datafeed.dukascopy.com/datafeed/XAUUSD"


def decode_day(payload, day):
    """Decode 24-byte BID M1 records; check range, OHLC consistency and alignment."""
    try:
        raw = lzma.decompress(payload)
    except lzma.LZMAError as exc:
        raise ValueError("Invalid Dukascopy LZMA candle data") from exc
    if not raw or len(raw) % RECORD.size:
        raise ValueError("Empty or malformed Dukascopy M1 data file")
    midnight = int(datetime.combine(day, time.min, timezone.utc).timestamp())
    bars = {}
    for seconds, opening, closing, low, high, volume in RECORD.iter_unpack(raw):
        if seconds >= 86400 or seconds % MINUTE:
            raise ValueError("M1 candle timestamp off UTC minute grid")
        prices = tuple(Decimal(v) / SCALE for v in (opening, high, low, closing))
        op, hi, lo, cl = prices
        if (not all(Decimal("100") <= x <= Decimal("25000") for x in prices)
                or hi < max(op, lo, cl) or lo > min(op, hi, cl)
                or not math.isfinite(volume) or volume < 0):
            raise ValueError("Invalid XAUUSD OHLCV in Dukascopy M1 file")
        ts = midnight + seconds
        candle = (ts, op, hi, lo, cl, Decimal(str(volume)))
        if ts in bars and bars[ts] != candle:
            raise ValueError("Conflicting M1 candle timestamp in Dukascopy file")
        bars[ts] = candle
    return [bars[key] for key in sorted(bars)]


def aggregate_m15(minute_bars):
    """Only form complete 15-minute bars, without interpolating missing M1 minutes."""
    groups = {}
    for bar in minute_bars:
        groups.setdefault(bar[0] - bar[0] % QUARTER, []).append(bar)
    complete = []
    incomplete = 0
    for start, group in sorted(groups.items()):
        expected = [start + index * MINUTE for index in range(15)]
        if [bar[0] for bar in group] != expected:
            incomplete += 1
            continue
        volumes = sum((bar[5] for bar in group), Decimal(0))
        complete.append((
            datetime.fromtimestamp(start, tz=timezone.utc),
            group[0][1], max(bar[2] for bar in group),
            min(bar[3] for bar in group), group[-1][4], volumes,
        ))
    return complete, incomplete


def fetch_day(day, retries=3):
    url = (f"{BASE_URL}/{day.year:04d}/{day.month-1:02d}/{day.day:02d}/"
           "BID_candles_min_1.bi5")
    for attempt in range(retries):
        try:
            request = Request(url, headers={"User-Agent": "RamonHistoricalResearch/1.0"})
            with urlopen(request, timeout=30) as response:
                content = response.read(5_000_001)
                if len(content) > 5_000_000:
                    raise ValueError("Historical day file unexpectedly large")
                if not content:
                    return None
                return content
        except HTTPError as exc:
            if exc.code == 404:
                return None
            if exc.code in (401, 403):
                raise CommandError(f"Dukascopy denied access (HTTP {exc.code}); no fallback to synthetic data") from exc
            error = exc
        except (URLError, TimeoutError, OSError) as exc:
            error = exc
        if attempt < retries - 1:
            clock.sleep(1 + attempt * 2)
    raise CommandError(f"Download failed for {day}: {error}")


class Command(BaseCommand):
    help = "Import externally sourced Dukascopy XAUUSD BID M1 -> validated M15 into separate research source."

    def add_arguments(self, parser):
        parser.add_argument("--start", required=True, help="Inclusive UTC YYYY-MM-DD")
        parser.add_argument("--end", required=True, help="Inclusive UTC YYYY-MM-DD")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--max-days", type=int, default=100)

    def handle(self, *args, **opts):
        try:
            start = date.fromisoformat(opts["start"])
            end = date.fromisoformat(opts["end"])
            total_days = (end - start).days + 1
            if not 1 <= total_days <= min(opts["max_days"], 100) or end > date.today():
                raise ValueError()
        except ValueError:
            raise CommandError("Invalid date range: use YYYY-MM-DD, not future dates, maximum 100 days")

        totals = {"days": 0, "no_data_days": 0, "m1": 0, "complete_m15": 0,
                  "incomplete_m15": 0, "inserted_m15": 0, "existing_m15": 0}
        fingerprints = hashlib.sha256()
        for delta in range(total_days):
            day = start + timedelta(days=delta)
            body = fetch_day(day)
            totals["days"] += 1
            if body is None:
                totals["no_data_days"] += 1
                self.stdout.write(f"{day}: no Dukascopy M1 file (HTTP 404), skipped")
                continue
            minutes = decode_day(body, day)
            bars, incomplete = aggregate_m15(minutes)
            totals["m1"] += len(minutes)
            totals["complete_m15"] += len(bars)
            totals["incomplete_m15"] += incomplete
            fingerprints.update(body)
            if not opts["dry_run"]:
                # Fail instead of overwriting different broker/source prices.
                with transaction.atomic():
                    timestamps = [bar[0] for bar in bars]
                    current = {
                        obj.timestamp: obj
                        for obj in MarketBar.objects.filter(source=SOURCE, symbol=SYMBOL,
                            timeframe="M15", timestamp__in=timestamps)
                    }
                    create = []
                    for stamp, opening, high, low, closing, volume in bars:
                        old = current.get(stamp)
                        if old is not None:
                            if any(getattr(old, key) != value for key, value in (
                                ("open", opening), ("high", high), ("low", low),
                                ("close", closing))):
                                raise CommandError(
                                    f"Conflicting historical OHLC at {stamp}; existing row not modified"
                                )
                            totals["existing_m15"] += 1
                            continue
                        create.append(MarketBar(source=SOURCE, symbol=SYMBOL,
                            timeframe="M15",timestamp=stamp, open=opening, high=high,
                            low=low,close=closing,volume=volume))
                    MarketBar.objects.bulk_create(create, batch_size=500)
                    totals["inserted_m15"] += len(create)
            self.stdout.write(f"{day}: M1={len(minutes)} M15={len(bars)} incomplete={incomplete}")
        self.stdout.write("Source: "+SOURCE+"; instrument: "+SYMBOL+"; BID candles only")
        self.stdout.write("Import summary: "+str(totals))
        self.stdout.write("Downloaded data SHA256 (concatenated compressed day files): "+fingerprints.hexdigest())
        self.stdout.write("Account units, spread, commissions and future broker fills are NOT supplied by this feed.")
        if opts["dry_run"]:
            self.stdout.write("DRY RUN: no database writes performed.")
