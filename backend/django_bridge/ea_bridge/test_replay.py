"""Historical replay endpoints stay read-only and use a single candle source."""
from datetime import datetime, timedelta, timezone
from django.test import TestCase
from .models import MarketBar

class HistoricalReplayTests(TestCase):
    def setUp(self):
        stamp = datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)
        for index in range(3):
            MarketBar.objects.create(
                source="broker-a", symbol="XAUUSD_l", timeframe="M5",
                timestamp=stamp + timedelta(minutes=5 * index),
                open="2600", high="2602", low="2599", close=str(2600 + index), volume="12",
            )
        MarketBar.objects.create(
            source="broker-b", symbol="XAUUSD_l", timeframe="M5",
            timestamp=stamp + timedelta(minutes=10),
            open="2700", high="2702", low="2699", close="2701", volume="5",
        )

    def test_page_is_accessible(self):
        response = self.client.get("/api/ea/v1/simulator")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Step Forward")

    def test_validated_bars_are_ordered_and_single_source(self):
        response = self.client.get("/api/ea/v1/simulator/bars", {
            "symbol": "XAUUSD_l", "timeframe": "M5", "source": "broker-a",
        })
        self.assertEqual(response.status_code, 200)
        rows = response.json()["bars"]
        self.assertEqual([item["close"] for item in rows], [2600., 2601., 2602.])
        self.assertEqual(len(rows), 3)

    def test_before_limit_is_inclusive(self):
        response = self.client.get("/api/ea/v1/simulator/bars", {
            "symbol": "XAUUSD_l", "timeframe": "M5", "source": "broker-a",
            "before": "2026-01-02T12:05:00Z",
        })
        self.assertEqual(len(response.json()["bars"]), 2)

    def test_invalid_queries_fail_closed(self):
        for params in [
            {"symbol": "XAUUSD_l", "timeframe": "INVALID"},
            {"symbol": "XAUUSD_l", "limit": "999999"},
            {"symbol": "XAUUSD_l", "before": "yesterday"},
            {"timeframe": "M5"},
        ]:
            with self.subTest(params=params):
                self.assertEqual(self.client.get("/api/ea/v1/simulator/bars", params).status_code, 400)

    def test_unknown_symbol_returns_empty_history(self):
        response = self.client.get("/api/ea/v1/simulator/bars", {"symbol": "UNKNOWN"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["bars"], [])
