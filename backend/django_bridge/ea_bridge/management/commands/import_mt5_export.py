"""Import validated broker M1 and complete M15 into an isolated research source."""
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ea_bridge.models import MarketBar
from ea_bridge.mt5_export import aggregate_m15, read_m1

SOURCE = "litefinance-mt5-live-export-20261122"
SYMBOL = "XAUUSD_l"


class Command(BaseCommand):
    help = "Audit an MT5 XAUUSD_l M1 CSV; --apply stores M1 and complete M15 in an isolated research source."

    def add_arguments(self, parser):
        parser.add_argument("csv", type=Path)
        parser.add_argument("--apply", action="store_true", help="Insert only after reviewing dry-run and timezone assumption")

    def handle(self, *args, **options):
        path = options["csv"]
        if f"_{SYMBOL}_M1_" not in path.name:
            raise CommandError(f"Expected an M1 export named for {SYMBOL}; inspect the symbol in MT5")
        try:
            minute_bars, report = read_m1(path)
            quarter_bars, incomplete = aggregate_m15(minute_bars)
        except (OSError, UnicodeError, ValueError) as exc:
            raise CommandError(f"MT5 CSV rejected: {exc}") from exc
        if not quarter_bars:
            raise CommandError("No complete M15 bars")
        self.stdout.write(f"SOURCE={SOURCE} SYMBOL={SYMBOL} UTC offset=broker +3 summer/+2 winter (provisional)")
        self.stdout.write(f"SHA256={report['sha256']} M1={report['rows']} first_utc={report['first']} last_utc={report['last']}")
        self.stdout.write(f"M15_complete={len(quarter_bars)} M15_incomplete_excluded={incomplete} gaps_minutes={report['gap_lengths_minutes']}")
        if not options["apply"]:
            self.stdout.write("DRY RUN: no database changes. Check broker timezone and overlap before --apply.")
            return
        # One transaction covers both timeframes. Existing candles are immutable.
        with transaction.atomic():
            for timeframe, bars in (("M1", minute_bars), ("M15", quarter_bars)):
                # Chunk both lookup and insert to bound memory and SQL parameters.
                created_count = existing_count = 0
                for offset in range(0, len(bars), 500):
                    batch = bars[offset:offset + 500]
                    existing = {row.timestamp: row for row in MarketBar.objects.filter(
                        source=SOURCE, symbol=SYMBOL, timeframe=timeframe,
                        timestamp__in=[bar[0] for bar in batch])}
                    create = []
                    for stamp, opening, high, low, closing, volume in batch:
                        old = existing.get(stamp)
                        if old is None:
                            create.append(MarketBar(source=SOURCE, symbol=SYMBOL, timeframe=timeframe,
                                                    timestamp=stamp, open=opening, high=high, low=low,
                                                    close=closing, volume=volume))
                            continue
                        if (old.open, old.high, old.low, old.close, old.volume) != (
                                opening, high, low, closing, volume):
                            raise CommandError(f"Existing {timeframe} candle differs at {stamp}; transaction rolled back")
                        existing_count += 1
                    MarketBar.objects.bulk_create(create, batch_size=500)
                    created_count += len(create)
                self.stdout.write(f"{timeframe}: inserted={created_count} already_present={existing_count}")
        self.stdout.write("Research source only. Existing EA candles and execution configuration untouched.")
