import json
from datetime import datetime, timezone
from decimal import Decimal

from django.test import TestCase

from ea_bridge.models import EAHeartbeat, MarketBar, StrategyDecisionRecord


class DashboardMarketSnapshotTests(TestCase):
    def test_snapshot_includes_decision_market_data_candles_and_safe_broker_context(self):
        EAHeartbeat.objects.create(
            instance_id="snapshot-ea",
            account_login="12345678",
            server_name="Demo-Server",
            symbol="XAUUSD_o",
            ea_version="1.06",
            status="TELEMETRY_OK",
            terminal_connected=True,
            trade_allowed=True,
            payload={
                "account_trade_mode": 0,
                "quote": {"bid": "2700.10", "ask": "2700.30", "point": "0.01"},
                "quote_time": "2026-11-04T06:00:00Z",
                "account": {
                    "balance": "10000.00",
                    "equity": "10025.00",
                    "currency": "USD",
                    "login": "must-not-be-exported",
                },
                "symbol_info": {"tick_size": "0.01", "tick_value": "1.0"},
                "positions": [{"symbol": "XAUUSD_o", "type": "BUY", "volume": "0.01"}],
            },
        )
        StrategyDecisionRecord.objects.create(
            decision_id="snapshot-decision",
            symbol="XAUUSD_o",
            action="WAIT",
            confidence=0.62,
            rationale_code="STOP_TOO_WIDE",
            model_version="django-signalflow-structural-v2",
            features={
                "atr": 2.5,
                "market_snapshot": {"symbol": "XAUUSD_o", "timeframe": "M15", "bid": 2700.1, "ask": 2700.3},
                "stages": {"risk_management": {"approved": False, "reason": "STOP_TOO_WIDE"}},
                "risk_calculation_inputs": {"risk_percent": 0.5},
            },
        )
        MarketBar.objects.create(
            source="snapshot-ea",
            symbol="XAUUSD_o",
            timeframe="M15",
            timestamp=datetime(2026, 11, 4, 6, 0, tzinfo=timezone.utc),
            open=Decimal("2699.00"),
            high=Decimal("2701.00"),
            low=Decimal("2698.50"),
            close=Decimal("2700.10"),
            volume=Decimal("12"),
        )

        response = self.client.get("/api/ea/v1/dashboard-snapshot")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)

        self.assertEqual(data["v"], 2)
        self.assertEqual(data["decision"]["reason"], "STOP_TOO_WIDE")
        self.assertEqual(data["market"]["candles_included"], 1)
        self.assertEqual(data["market"]["ohlcv"][0][4], "2700.1000000000")
        self.assertEqual(data["broker"]["account"]["balance"], "10000.00")
        self.assertNotIn("account_login", data["broker"])
        self.assertNotIn("login", data["broker"]["account"])
        self.assertEqual(data["thresholds"]["profile"], "demo")

class DashboardRangeAnalysisTests(TestCase):
    def test_range_analysis_uses_saved_decisions_and_account_scoped_closed_outcomes(self):
        EAHeartbeat.objects.create(
            instance_id="range-ea",
            account_login="range-account",
            server_name="Demo-Server",
            symbol="XAUUSD_o",
            status="TELEMETRY_OK",
            terminal_connected=True,
            trade_allowed=True,
            payload={"account_trade_mode": 0},
        )
        StrategyDecisionRecord.objects.create(
            decision_id="range-approved",
            symbol="XAUUSD_o",
            action="WAIT",
            confidence=0.8,
            model_version="test",
            features={"stages": {"opportunities": {
                "approved": True,
                "status": "APPROVED",
                "setup": {
                    "type": "RANGE_EDGE_FADE", "side": "BUY",
                    "range_high": 2710.0, "range_low": 2690.0,
                    "range_width_atr": 4.0,
                    "boundary_touches": {"upper": 3, "lower": 2},
                },
            }}},
        )
        StrategyDecisionRecord.objects.create(
            decision_id="range-deferred",
            symbol="XAUUSD_o",
            action="WAIT",
            confidence=0.5,
            model_version="test",
            features={"stages": {"opportunities": {
                "status": "DEFERRED",
                "regime": {"label": "RANGING"},
                "setup": {"type": "RANGE_EDGE_FADE", "side": "BUY", "range_high": 2710.0, "range_low": 2690.0},
            }}},
        )
        from ea_bridge.models import LearningTradeOutcome
        LearningTradeOutcome.objects.create(
            trade_id="range-trade-1",
            decision_id="range-approved",
            instance_id="range-ea",
            account_login="range-account",
            symbol="XAUUSD_o",
            realized_pnl=Decimal("25.50"),
            r_multiple=Decimal("1.50"),
            closed_at=datetime(2026, 11, 4, 7, 0, tzinfo=timezone.utc),
            tags={"setup": "RANGE_EDGE_FADE"},
        )

        response = self.client.get("/api/ea/v1/dashboard-data")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        analysis = data["range_analysis"]
        self.assertEqual(analysis["detected"], 2)
        self.assertEqual(analysis["approved"], 1)
        self.assertEqual(analysis["deferred"], 1)
        self.assertEqual(analysis["closed_trades"], 1)
        self.assertEqual(analysis["winning_trades"], 1)
        self.assertEqual(analysis["win_rate_percent"], 100.0)
        self.assertEqual(analysis["net_pnl"], "25.50000000")
        self.assertEqual(analysis["average_r_multiple"], 1.5)
        self.assertEqual(analysis["latest_setup"]["side"], "BUY")
