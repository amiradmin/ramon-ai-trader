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

        self.assertEqual(data["schema_version"], "ramon-market-snapshot-v1")
        self.assertEqual(data["decision"]["rationale_code"], "STOP_TOO_WIDE")
        self.assertEqual(data["market"]["bars_count"], 1)
        self.assertEqual(data["market"]["bars"][0]["close"], "2700.1000000000")
        self.assertEqual(data["broker"]["account"]["balance"], "10000.00")
        self.assertNotIn("account_login", data["broker"])
        self.assertNotIn("login", data["broker"]["account"])
        self.assertEqual(data["thresholds"]["profile"], "demo")
