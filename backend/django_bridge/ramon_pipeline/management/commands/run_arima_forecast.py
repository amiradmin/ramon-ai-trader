"""Run an ARIMA close-price forecast from completed MarketBar candles."""
import json

from django.core.management.base import BaseCommand, CommandError

from ramon_pipeline.arima import forecast_market_bars


class Command(BaseCommand):
    help = "Evaluate ARIMA on a chronological candle holdout and forecast the next candle close."

    def add_arguments(self, parser):
        parser.add_argument("--symbol", required=True, help="Exact market symbol, e.g. XAUUSD_o")
        parser.add_argument("--timeframe", default="M15", help="Candle timeframe (default: M15)")
        parser.add_argument("--source", default=None, help="Optional EA instance/source filter")
        parser.add_argument("--limit", type=int, default=1000, help="Maximum candles to use")
        parser.add_argument("--test-size", type=int, default=20, help="Chronological holdout size")
        parser.add_argument("--p", type=int, default=1, help="AR order")
        parser.add_argument("--d", type=int, default=1, help="Differencing order")
        parser.add_argument("--q", type=int, default=1, help="MA order")

    def handle(self, *args, **options):
        if min(options["p"], options["d"], options["q"]) < 0:
            raise CommandError("ARIMA p, d and q must be non-negative")
        try:
            result = forecast_market_bars(
                symbol=options["symbol"],
                timeframe=options["timeframe"],
                source=options["source"],
                limit=options["limit"],
                test_size=options["test_size"],
                order=(options["p"], options["d"], options["q"]),
            )
        except (ValueError, ArithmeticError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(result.as_dict(), indent=2, sort_keys=True))
