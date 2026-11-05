"""Conservative, explicit retention for high-volume market candle history.

The command is dry-run by default. It never deletes market data unless --apply
is supplied explicitly.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from ea_bridge.models import MarketBar


DEFAULT_RETENTION_DAYS = {
    "M1": 730,    # 2 years
    "M5": 1095,   # 3 years
    "M15": 1825,  # 5 years
    "H1": 1825,   # 5 years
}


class Command(BaseCommand):
    help = "Report or explicitly prune old MarketBar candles by timeframe."

    def add_arguments(self, parser):
        parser.add_argument("--symbol", default="XAUUSD_o")
        parser.add_argument("--source", default="")
        parser.add_argument("--apply", action="store_true", help="Actually delete expired candles; default is dry-run.")
        parser.add_argument("--m1-days", type=int, default=DEFAULT_RETENTION_DAYS["M1"])
        parser.add_argument("--m5-days", type=int, default=DEFAULT_RETENTION_DAYS["M5"])
        parser.add_argument("--m15-days", type=int, default=DEFAULT_RETENTION_DAYS["M15"])
        parser.add_argument("--h1-days", type=int, default=DEFAULT_RETENTION_DAYS["H1"])

    def handle(self, *args, **options):
        symbol = str(options["symbol"]).strip()
        source = str(options["source"]).strip()
        if not symbol:
            raise ValueError("--symbol cannot be empty")

        retention = {
            "M1": options["m1_days"],
            "M5": options["m5_days"],
            "M15": options["m15_days"],
            "H1": options["h1_days"],
        }
        if any(days < 1 for days in retention.values()):
            raise ValueError("retention periods must be positive day counts")

        now = timezone.now()
        total = 0
        mode = "APPLY" if options["apply"] else "DRY-RUN"
        self.stdout.write(f"MarketBar retention mode={mode} symbol={symbol} source={source or '*'}")
        for timeframe, days in retention.items():
            cutoff = now - timedelta(days=days)
            query = MarketBar.objects.filter(
                symbol=symbol, timeframe=timeframe, timestamp__lt=cutoff,
            )
            if source:
                query = query.filter(source=source)
            count = query.count()
            total += count
            if options["apply"] and count:
                deleted, _details = query.delete()
                self.stdout.write(f"{timeframe}: deleted={deleted} cutoff_utc={cutoff.isoformat()}")
            else:
                self.stdout.write(f"{timeframe}: expired={count} cutoff_utc={cutoff.isoformat()}")

        self.stdout.write(
            self.style.SUCCESS(
                f"{mode} complete; expired_candles={total}"
                + ("" if options["apply"] else "; no rows were deleted (use --apply to prune)")
            )
        )
