import json
from datetime import datetime, timezone
from decimal import Decimal

from django.test import TestCase

from ea_bridge.models import EAHeartbeat, MarketBar, RiskVerdictRecord, StrategyDecisionRecord


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


class DashboardRiskVerdictTests(TestCase):
    def test_dashboard_uses_persisted_risk_verdict_instead_of_pipeline_stage_pass(self):
        StrategyDecisionRecord.objects.create(
            decision_id="risk-verdict-decision",
            symbol="XAUUSD_o",
            action="BUY",
            confidence=0.91,
            rationale_code="SIGNALFLOW_APPROVED",
            model_version="django-signalflow-structural-v2",
            features={
                "approved": True,
                "stages": {
                    "opportunities": {"approved": True, "status": "PASSED"},
                    "entry_timing": {"approved": True, "status": "PASSED"},
                    # This earlier stage result must not override the final verdict.
                    "risk_management": {"approved": True, "status": "PASSED"},
                },
            },
        )
        RiskVerdictRecord.objects.create(
            decision_id="risk-verdict-decision",
            approved=False,
            reason_code="UPSTREAM_STAGE_REJECTED",
            approved_volume=Decimal("0"),
            details={
                "risk_stage": {
                    "approved": False,
                    "status": "REJECTED",
                    "reason": "UPSTREAM_STAGE_REJECTED",
                    "checks": [
                        {
                            "name": "risk_input_validation",
                            "passed": None,
                            "status": "NOT_EVALUATED",
                        }
                    ],
                }
            },
        )

        response = self.client.get("/api/ea/v1/dashboard-data")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        decision = data["decisions"][0]

        self.assertFalse(decision["risk_verdict"]["approved"])
        self.assertEqual(decision["risk_verdict"]["status"], "NOT_EVALUATED")
        self.assertEqual(decision["risk_verdict"]["reason"], "UPSTREAM_STAGE_REJECTED")
        self.assertEqual(
            decision["risk_verdict"]["checks"][0]["status"],
            "NOT_EVALUATED",
        )
        # Preserve the original pipeline evidence; the client applies the
        # authoritative persisted verdict when rendering the Risk Management stage.
        self.assertTrue(decision["features"]["stages"]["risk_management"]["approved"])

    def test_dashboard_reports_missing_final_risk_verdict_as_not_evaluated(self):
        StrategyDecisionRecord.objects.create(
            decision_id="missing-risk-verdict",
            symbol="XAUUSD_o",
            action="BUY",
            confidence=0.8,
            rationale_code="SIGNALFLOW_APPROVED",
            model_version="django-signalflow-structural-v2",
            features={
                "approved": True,
                "stages": {
                    "opportunities": {"approved": True, "status": "PASSED"},
                    "entry_timing": {"approved": True, "status": "PASSED"},
                    "risk_management": {"approved": True, "status": "PASSED"},
                },
            },
        )

        response = self.client.get("/api/ea/v1/dashboard-data")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertIsNone(data["decisions"][0]["risk_verdict"])
