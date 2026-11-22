"""Historical replay endpoints stay read-only and use a single candle source."""
from datetime import datetime, timedelta, timezone
from django.test import TestCase
from .models import MarketBar, LearningTradeOutcome

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

class ReplayRecordedPerformanceTests(TestCase):
    def setUp(self):
        for trade_id, minute, pnl, account in [
            ("trade-a", 5, "12.5", "one"),
            ("trade-b", 10, "-4.0", "one"),
            ("trade-c", 15, "100.0", "one"),
        ]:
            LearningTradeOutcome.objects.create(
                trade_id=trade_id, symbol="XAUUSD_o", account_login=account,
                realized_pnl=pnl,
                closed_at=datetime(2026, 1, 2, 12, minute, tzinfo=timezone.utc),
            )

    def stats(self, through, start="2026-01-02T12:00:00Z"):
        return self.client.get("/api/ea/v1/simulator/recorded-stats", {
            "symbol": "XAUUSD_o", "start": start, "through": through,
        })

    def test_cursor_only_counts_closed_trades_up_to_position(self):
        first = self.stats("2026-01-02T12:05:00Z").json()
        self.assertEqual(first["trade_count"], 1)
        self.assertEqual(first["win_rate_percent"], 100.0)
        self.assertEqual(first["total_gain"], "12.50000000")
        second = self.stats("2026-01-02T12:10:00Z").json()
        self.assertEqual(second["trade_count"], 2)
        self.assertEqual(second["win_rate_percent"], 50.0)
        self.assertEqual(second["total_gain"], "8.50000000")

    def test_no_trade_is_not_zero_win_rate(self):
        result = self.stats("2026-01-02T12:04:00Z").json()
        self.assertEqual(result["trade_count"], 0)
        self.assertIsNone(result["win_rate_percent"])
        self.assertIsNone(result["total_gain"])

    def test_mixed_accounts_hide_net_gain(self):
        LearningTradeOutcome.objects.create(
            trade_id="other-account", symbol="XAUUSD_o",
            account_login="two", realized_pnl="20",
            closed_at=datetime(2026, 1, 2, 12, 8, tzinfo=timezone.utc),
        )
        result = self.stats("2026-01-02T12:10:00Z").json()
        self.assertTrue(result["mixed_accounts"])
        self.assertIsNone(result["total_gain"])

    def test_invalid_dates_are_rejected(self):
        self.assertEqual(self.stats("not-a-date").status_code, 400)
        self.assertEqual(self.stats("2026-01-02T11:00:00Z").status_code, 400)
