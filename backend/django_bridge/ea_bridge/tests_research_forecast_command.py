import json
from datetime import datetime, timedelta, timezone
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command, CommandError
from django.test import TestCase

from ea_bridge.models import MarketBar


class ReadOnlyResearchForecastCommandTests(TestCase):
    def setUp(self):
        start = datetime(2026, 10, 1, tzinfo=timezone.utc)
        rows = []
        for index in range(60):
            close = 2300 + index * 0.1
            rows.append(MarketBar(
                source="test-feed", symbol="XAUUSD_o", timeframe="M15",
                timestamp=start + timedelta(minutes=15 * index),
                open=close - 0.05, high=close + 0.2,
                low=close - 0.2, close=close, volume=10 + index,
            ))
        MarketBar.objects.bulk_create(rows)

    @patch("ea_bridge.management.commands.research_forecast.evaluate_regression_forecasts")
    def test_reads_existing_marketbars_and_never_writes_or_authorizes(self, evaluate):
        evaluate.return_value = {
            "ridge": type("Report", (), {"as_dict": lambda self: {
                "model": "ridge", "mae": 0.001, "rmse": 0.002
            }})(),
        }
        before = MarketBar.objects.count()
        output = StringIO()
        call_command(
            "research_forecast", symbol="XAUUSD_o", timeframe="M15",
            source="test-feed", limit=60, models="ridge", stdout=output,
        )
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["mode"], "READ_ONLY_RESEARCH")
        self.assertEqual(payload["candles_read"], 60)
        self.assertIn("ridge", payload["models"])
        self.assertFalse(payload["trading_authorization"])
        self.assertFalse(payload["strategy_mutation"])
        self.assertFalse(payload["database_writes"])
        self.assertEqual(MarketBar.objects.count(), before)
        evaluate.assert_called_once()

    def test_rejects_unknown_model_without_touching_marketbars(self):
        before = MarketBar.objects.count()
        with self.assertRaises(CommandError):
            call_command("research_forecast", models="tensorflow_magic")
        self.assertEqual(MarketBar.objects.count(), before)
