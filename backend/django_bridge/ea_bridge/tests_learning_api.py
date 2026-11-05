import json
from datetime import datetime, timedelta, timezone

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone as django_timezone

from ea_bridge.learning_store import DjangoOutcomeStore
from ea_bridge.models import EACommand, DecisionThresholdConfig, LearningRecommendation, LearningTradeOutcome, RiskVerdictRecord, StrategyDecisionRecord
from ramon_pipeline.learning import EntryExitAnalyzer, LearningEngine, OptimizationEngine
from ramon_pipeline.signalflow.learning import InMemoryOutcomeStore, Learning


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
            model_version="test-v1", features={"strategy_profile": "BALANCED", "stages": {"opportunities": {"regime": {"label": "BULLISH"}, "setup": {"type": "PULLBACK"}, "signal_id": "signal-001", "score": 0.82, "reason": "OPPORTUNITY_FOUND_WITH_FORECAST_CONFIRMATION"}}},
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
        self.assertEqual(row.tags["command_id"], "ramon-open-" + "a" * 32)
        self.assertEqual(row.tags["broker_ticket"], "987654321")
        self.assertEqual(row.instance_id, "ramon-command-ea-1")
        self.assertEqual(row.account_login, "123456")
        self.assertEqual(row.symbol, "XAUUSD_o")
        self.assertEqual(row.decision_snapshot["action"], "BUY")
        self.assertEqual(row.tags["regime"], "BULLISH")
        self.assertEqual(row.tags["setup_type"], "PULLBACK")
        self.assertEqual(row.tags["strategy_profile"], "BALANCED")
        self.assertTrue(row.tags["forecast_confirmed"])


    @override_settings(EA_BEARER_TOKEN="learning-test-token")
    def test_command_linked_outcome_uses_django_risk_and_broker_fill_slippage(self):
        decision = StrategyDecisionRecord.objects.create(
            decision_id="decision-risk-001", symbol="XAUUSD_o", action="BUY",
            confidence=0.8, rationale_code="SIGNALFLOW_APPROVED",
            model_version="test-v1",
            features={
                "strategy_profile": "BALANCED",
                "market_snapshot": {"bid": 2300.10, "ask": 2300.20, "point": 0.01},
                "stages": {"opportunities": {"regime": {"label": "BULLISH"}}},
            },
        )
        verdict = RiskVerdictRecord.objects.create(
            decision_id=decision.decision_id, approved=True,
            reason_code="RISK_APPROVED_ADVISORY_ONLY",
            approved_volume="0.01", stop_loss="2290.0", take_profit="2320.0",
            details={"risk_stage": {"estimated_risk": 12.5, "volume": 0.01}},
        )
        EACommand.objects.create(
            command_id="ramon-open-risk-001",
            instance_id="ramon-command-ea-1", account_login="123456",
            symbol="XAUUSD_o", action=EACommand.Action.OPEN,
            payload={"side": "BUY", "volume": 0.01},
            decision_id=decision.decision_id, risk_verdict_id=str(verdict.pk),
            execution_authorized=True, state=EACommand.State.EXECUTED,
            expires_at=django_timezone.now() + timedelta(minutes=1),
            result={"broker_ticket": "risk-ticket-001"},
        )
        payload = {
            "trade_id": "123456:position-risk-001",
            "broker_ticket": "risk-ticket-001", "command_id": "ramon-open-risk-001",
            "instance_id": "ramon-command-ea-1", "account_login": "123456",
            "symbol": "XAUUSD_o", "side": "BUY",
            "entry_price": 2300.25, "realized_pnl": "6.25",
            "opened_at": "2026-11-05T10:00:00Z",
            "closed_at": "2026-11-05T11:00:00Z",
        }
        response = self.client.post(
            self.url, data=json.dumps(payload), content_type="application/json", **self.headers
        )
        self.assertEqual(response.status_code, 201, response.content)
        row = LearningTradeOutcome.objects.get(trade_id="123456:position-risk-001")
        self.assertEqual(row.decision_id, decision.decision_id)
        self.assertEqual(row.risk_amount, 12.5)
        self.assertAlmostEqual(float(row.r_multiple), 0.5)
        self.assertEqual(row.tags["command_id"], "ramon-open-risk-001")
        self.assertEqual(row.tags["broker_ticket"], "risk-ticket-001")
        self.assertEqual(row.execution_metrics["slippage_points"], 5.0)

    @override_settings(EA_BEARER_TOKEN="learning-test-token")
    def test_command_without_approved_risk_keeps_r_multiple_unavailable(self):
        decision = StrategyDecisionRecord.objects.create(
            decision_id="decision-no-risk-001", symbol="XAUUSD_o", action="BUY",
            confidence=0.8, rationale_code="SIGNALFLOW_APPROVED",
            model_version="test-v1", features={"market_snapshot": {"ask": 2300.2, "point": 0.01}},
        )
        verdict = RiskVerdictRecord.objects.create(
            decision_id=decision.decision_id, approved=False,
            reason_code="RISK_REJECTED", approved_volume="0",
            details={"risk_stage": {"estimated_risk": 12.5, "volume": 0.01}},
        )
        EACommand.objects.create(
            command_id="ramon-open-no-risk-001",
            instance_id="ramon-command-ea-1", account_login="123456",
            symbol="XAUUSD_o", action=EACommand.Action.OPEN,
            payload={"side": "BUY", "volume": 0.01},
            decision_id=decision.decision_id, risk_verdict_id=str(verdict.pk),
            execution_authorized=True, state=EACommand.State.EXECUTED,
            expires_at=django_timezone.now() + timedelta(minutes=1),
            result={"broker_ticket": "no-risk-ticket-001"},
        )
        payload = {
            "trade_id": "123456:position-no-risk-001",
            "broker_ticket": "no-risk-ticket-001",
            "instance_id": "ramon-command-ea-1", "account_login": "123456",
            "symbol": "XAUUSD_o", "realized_pnl": "6.25", "risk_amount": "9999",
            "opened_at": "2026-11-05T10:00:00Z",
            "closed_at": "2026-11-05T11:00:00Z",
        }
        response = self.client.post(
            self.url, data=json.dumps(payload), content_type="application/json", **self.headers
        )
        self.assertEqual(response.status_code, 201, response.content)
        row = LearningTradeOutcome.objects.get(trade_id="123456:position-no-risk-001")
        self.assertIsNone(row.risk_amount)
        self.assertIsNone(row.r_multiple)

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

    def test_main_dashboard_links_to_learning_engine(self):
        response = self.client.get("/api/ea/v1/dashboard")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/api/ea/v1/learning/dashboard")

    def test_learning_dashboard_renders_analysis_sections(self):
        response = self.client.get("/api/ea/v1/learning/dashboard")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Learning Engine")
        self.assertContains(response, "Recent completed trades")
        self.assertContains(response, "Train Six Trade Models")
        # These analysis panels were deliberately removed from the current
        # learning dashboard; keep the contract aligned with the rendered UI.
        for removed_section in (
            "Performance by market regime",
            "Evidence review",
            "Decision attribution",
            "Human review queue",
            "Data quality",
            "Equity progression",
            "Entry & exit diagnostics",
            "Strategy profile evaluation",
        ):
            self.assertNotContains(response, removed_section)

    def test_learning_overview_exposes_modular_analysis_report(self):
        response = self.client.get("/api/ea/v1/learning/overview")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("analysis", payload)
        self.assertFalse(payload["analysis"]["auto_apply"])
        self.assertFalse(payload["analysis"]["strategy_mutation_enabled"])
        self.assertIn("decisions", payload["analysis"])
        self.assertIn("entry_exit", payload["analysis"])

    def test_learning_package_collects_and_analyzes_without_strategy_mutation(self):
        engine = LearningEngine(InMemoryOutcomeStore())
        result = engine.collect({
            "trade_id": "package-trade-1", "realized_pnl": 12,
            "risk_amount": 10, "closed_at": 1_800_000_000,
            "decision_id": "package-decision-1", "side": "BUY",
            "regime": "BULLISH",
        })
        self.assertTrue(result["accepted"])
        report = engine.analyze(min_samples=10)
        self.assertEqual(report["performance"]["overall"]["trades"], 1)
        self.assertEqual(report["decisions"]["linked_outcomes"], 1)
        self.assertFalse(report["auto_apply"])
        self.assertFalse(report["strategy_mutation_enabled"])

    def test_entry_exit_analysis_does_not_treat_missing_metrics_as_zero(self):
        report = EntryExitAnalyzer().analyze([{"execution_metrics": {}}])
        self.assertEqual(report["metrics"]["mae"]["observations"], 0)
        self.assertEqual(report["metrics"]["mae"]["unavailable"], 1)
        self.assertIsNone(report["metrics"]["mae"]["average"])

    def test_optimizer_only_returns_human_review_eligibility(self):
        result = OptimizationEngine().evaluate_candidate(
            baseline={"trades": 120, "expectancy": 0.1, "max_drawdown": 20},
            candidate={"trades": 120, "expectancy": 0.2, "max_drawdown": 18},
        )
        self.assertTrue(result["eligible_for_review"])
        self.assertFalse(result["auto_apply"])
        self.assertFalse(result["live_parameters_changed"])

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
