import json
from datetime import datetime, timedelta, timezone

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone as django_timezone

from ea_bridge.learning_store import DjangoOutcomeStore
from ea_bridge.models import EACommand, DecisionThresholdConfig, LearningRecommendation, LearningTradeOutcome, StrategyDecisionRecord
from ramon_pipeline.signalflow.learning import Learning


class LearningOutcomeApiTests(TestCase):
    url = "/api/ea/v1/learning/outcomes"
    headers = {"HTTP_AUTHORIZATION": "Bearer learning-test-token"}

    @override_settings(EA_BEARER_TOKEN="learning-test-token")
    def test_records_closed_outcome_and_snapshots_decision(self):
        from ea_bridge.models import StrategyDecisionRecord
        StrategyDecisionRecord.objects.create(
            decision_id="decision-001", symbol="XAUUSD_o", action="BUY",
            confidence=0.8, rationale_code="SIGNALFLOW_APPROVED",
            model_version="test-v1", features={"strategy_profile": "BALANCED", "stages": {"opportunities": {"regime": {"label": "BULLISH"}, "setup": {"type": "PULLBACK"}, "signal_id": "signal-001", "score": 0.82, "reason": "OPPORTUNITY_FOUND_WITH_FORECAST_CONFIRMATION"}}},
        )
        payload = {
            "trade_id": "deal-001", "decision_id": "decision-001",
            "symbol": "XAUUSD_o", "realized_pnl": "15.50", "risk_amount": "10",
            "opened_at": "2026-11-03T10:00:00Z", "closed_at": "2026-11-03T11:00:00Z",
            "side": "BUY", "regime": "BULLISH", "exit_reason": "TP",
            "mae": 2.0, "mfe": 18.0, "entry_efficiency": 0.8,
            "exit_efficiency": 0.9, "commission": -0.5, "swap": 0,
        }
        response = self.client.post(self.url, data=json.dumps(payload), content_type="application/json", **self.headers)
        self.assertEqual(response.status_code, 201, response.content)
        row = LearningTradeOutcome.objects.get(trade_id="deal-001")
        self.assertEqual(row.decision_snapshot["action"], "BUY")
        self.assertAlmostEqual(float(row.r_multiple), 1.55)
        self.assertEqual(row.execution_metrics["mae"], 2.0)
        self.assertEqual(row.tags["regime"], "BULLISH")

    @override_settings(EA_BEARER_TOKEN="learning-test-token")
    def test_opening_order_ticket_resolves_decision_when_broker_truncates_comment(self):
        StrategyDecisionRecord.objects.create(
            decision_id="decision-ticket-001", symbol="XAUUSD_o", action="BUY",
            confidence=0.75, rationale_code="SIGNALFLOW_APPROVED",
            model_version="test-v1", features={"regime": "BULLISH"},
        )
        EACommand.objects.create(
            command_id="ramon-open-" + "a" * 32,
            instance_id="ramon-command-ea-1", account_login="123456",
            symbol="XAUUSD_o", action=EACommand.Action.OPEN,
            payload={"side": "BUY", "volume": 0.01},
            decision_id="decision-ticket-001", execution_authorized=True,
            state=EACommand.State.EXECUTED,
            expires_at=django_timezone.now() + timedelta(minutes=1),
            result={"broker_ticket": "987654321"},
        )
        payload = {
            "trade_id": "123456:5550001", "broker_ticket": "987654321",
            "instance_id": "ramon-command-ea-1", "account_login": "123456",
            "symbol": "XAUUSD_o", "side": "BUY", "exit_reason": "TP",
            "realized_pnl": "12.75", "opened_at": "2026-11-03T10:00:00Z",
            "closed_at": "2026-11-03T11:00:00Z",
        }
        response = self.client.post(
            self.url, data=json.dumps(payload), content_type="application/json", **self.headers
        )
        self.assertEqual(response.status_code, 201, response.content)
        row = LearningTradeOutcome.objects.get(trade_id="123456:5550001")
        self.assertEqual(row.decision_id, "decision-ticket-001")
        self.assertEqual(row.decision_snapshot["action"], "BUY")
        self.assertEqual(row.tags["regime"], "BULLISH")
        self.assertEqual(row.tags["setup_type"], "PULLBACK")
        self.assertEqual(row.tags["strategy_profile"], "BALANCED")
        self.assertTrue(row.tags["forecast_confirmed"])

    @override_settings(EA_BEARER_TOKEN="learning-test-token")
    def test_duplicate_is_idempotent_and_conflict_does_not_overwrite(self):
        payload = {"trade_id": "deal-002", "realized_pnl": 5, "closed_at": "2026-11-03T11:00:00Z"}
        first = self.client.post(self.url, data=json.dumps(payload), content_type="application/json", **self.headers)
        duplicate = self.client.post(self.url, data=json.dumps(payload), content_type="application/json", **self.headers)
        conflict = self.client.post(self.url, data=json.dumps({**payload, "realized_pnl": -5}), content_type="application/json", **self.headers)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(LearningTradeOutcome.objects.get(trade_id="deal-002").realized_pnl, 5)

    @override_settings(EA_BEARER_TOKEN="learning-test-token")
    def test_authentication_and_close_time_are_required(self):
        payload = {"trade_id": "deal-003", "realized_pnl": 5}
        denied = self.client.post(self.url, data=json.dumps(payload), content_type="application/json")
        invalid = self.client.post(self.url, data=json.dumps(payload), content_type="application/json", **self.headers)
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(invalid.status_code, 400)

    def test_persistent_store_supports_summary_and_regime_segments(self):
        engine = Learning(store=DjangoOutcomeStore())
        for trade_id, pnl, minute in (("a", 10, 1), ("b", -5, 2), ("c", 10, 3)):
            result = engine.observe({
                "trade_id": trade_id, "realized_pnl": pnl, "risk_amount": 10,
                "closed_at": datetime(2026, 11, 3, 10, minute, tzinfo=timezone.utc).timestamp(),
                "regime": "BULLISH", "side": "BUY",
            })
            self.assertTrue(result["accepted"], result)
        summary = engine.summary()
        self.assertEqual(summary["overall"]["trades"], 3)
        self.assertAlmostEqual(summary["overall"]["expectancy"], 5)
        self.assertEqual(summary["by_regime"]["BULLISH"]["trades"], 3)

    def test_learning_models_are_registered_read_only_in_admin(self):
        self.assertIn(LearningTradeOutcome, admin.site._registry)
        self.assertIn(LearningRecommendation, admin.site._registry)
        self.assertFalse(admin.site._registry[LearningTradeOutcome].has_add_permission(None))

    def test_assisted_review_never_changes_strategy_thresholds(self):
        user = get_user_model().objects.create_user(username="reviewer", password="pass", is_staff=True)
        recommendation = LearningRecommendation.objects.create(
            fingerprint="a" * 64, finding_code="SEGMENT_UNDERPERFORMS",
            title="Test finding", detail="Hypothesis only", evidence={"trades": 40},
        )
        before = list(DecisionThresholdConfig.objects.values_list("profile", "values"))
        self.client.force_login(user)
        response = self.client.post(
            f"/api/ea/v1/learning/recommendations/{recommendation.id}/review",
            data=json.dumps({"decision": "ACCEPT", "note": "Run a controlled backtest"}),
            content_type="application/json",
        )
        recommendation.refresh_from_db()
        after = list(DecisionThresholdConfig.objects.values_list("profile", "values"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(recommendation.status, LearningRecommendation.Status.ACCEPTED_FOR_BACKTEST)
        self.assertEqual(before, after)
        self.assertFalse(response.json()["strategy_changed"])

    def test_non_staff_cannot_review_recommendations(self):
        user = get_user_model().objects.create_user(username="viewer", password="pass", is_staff=False)
        self.client.force_login(user)
        response = self.client.post(
            "/api/ea/v1/learning/recommendations/1/review",
            data=json.dumps({"decision": "REJECT"}), content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
