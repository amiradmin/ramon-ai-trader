"""Read-only evaluation of forecasting models against existing MarketBar rows."""
from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from ea_bridge.models import MarketBar
from ramon_pipeline.research_forecasting import evaluate_regression_forecasts
from ramon_pipeline.signalflow.forecasting import (
    forecast_market_bars,
    forecast_market_bars_sarima,
)


class Command(BaseCommand):
    help = "Read MarketBar candles and print research-only forecast diagnostics; never writes data."

    def add_arguments(self, parser):
        parser.add_argument("--symbol", default="XAUUSD_o")
        parser.add_argument("--timeframe", default="M15")
        parser.add_argument("--source", default=None)
        parser.add_argument("--limit", type=int, default=500)
        parser.add_argument("--test-size", type=int, default=20)
        parser.add_argument("--horizon-bars", type=int, default=4)
        parser.add_argument("--models", default="arima,sarima,ridge,random_forest",
                            help="Comma-separated: arima,sarima,ridge,random_forest")

    def handle(self, *args, **options):
        symbol = options["symbol"].strip()
        timeframe = options["timeframe"].strip().upper()
        source = options["source"]
        limit = options["limit"]
        test_size = options["test_size"]
        horizon = options["horizon_bars"]
        selected = {item.strip().lower() for item in options["models"].split(",") if item.strip()}
        allowed = {"arima", "sarima", "ridge", "random_forest"}
        if not selected or selected-allowed:
            raise CommandError(f"--models must contain only: {', '.join(sorted(allowed))}")
        if limit < 50 or test_size < 1 or horizon < 1:
            raise CommandError("--limit must be >= 50; --test-size and --horizon-bars must be positive")

        query = MarketBar.objects.filter(symbol=symbol, timeframe=timeframe)
        if source:
            query = query.filter(source=source)
        bars = list(query.order_by("-timestamp").only(
            "timestamp", "open", "high", "low", "close", "volume"
        )[:limit])
        bars.reverse()
        if len(bars) < 50:
            raise CommandError(
                f"Only {len(bars)} MarketBar rows found for {symbol} {timeframe}; need at least 50"
            )
        candles = [{
            "timestamp": bar.timestamp.isoformat(),
            "open": float(bar.open), "high": float(bar.high),
            "low": float(bar.low), "close": float(bar.close),
            "tick_volume": float(bar.volume),
        } for bar in bars]

        result = {
            "mode": "READ_ONLY_RESEARCH",
            "database_source": "existing MarketBar ORM rows",
            "symbol": symbol, "timeframe": timeframe, "source_filter": source,
            "candles_read": len(bars),
            "first_bar": bars[0].timestamp.isoformat(),
            "last_bar": bars[-1].timestamp.isoformat(),
            "models": {},
            "trading_authorization": False,
            "strategy_mutation": False,
            "database_writes": False,
        }
        failures = {}
        if "arima" in selected:
            try:
                result["models"]["arima"] = forecast_market_bars(
                    symbol=symbol, timeframe=timeframe, source=source,
                    limit=limit, test_size=test_size,
                ).as_dict()
            except Exception as exc:
                failures["arima"] = str(exc)
        if "sarima" in selected:
            try:
                result["models"]["sarima"] = forecast_market_bars_sarima(
                    symbol=symbol, timeframe=timeframe, source=source,
                    limit=limit, test_size=test_size,
                ).as_dict()
            except Exception as exc:
                failures["sarima"] = str(exc)
        regression_selected = selected & {"ridge", "random_forest"}
        if regression_selected:
            try:
                reports = evaluate_regression_forecasts(
                    candles, horizon_bars=horizon
                )
                result["models"].update({
                    name: report.as_dict()
                    for name, report in reports.items() if name in regression_selected
                })
            except Exception as exc:
                failures["regression"] = str(exc)
        result["failures"] = failures
        self.stdout.write(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
        if failures:
            raise CommandError("One or more research models failed; see JSON diagnostics above.")
