from django.test import TestCase

from .decision import _apply_demo_risk_policy, _build_pipeline
from .models import DecisionThresholdConfig


class DemoRiskPolicyTests(TestCase):
    def test_demo_uses_dashboard_owned_per_trade_risk_budget(self):
        pipeline = _build_pipeline(demo_mode=True)
        market = {"risk_percent": 0.20882145}

        policy = _apply_demo_risk_policy(market, pipeline, demo_mode=True)

        self.assertEqual(market["risk_percent"], 0.5)
        self.assertEqual(policy["source"], "SERVER_DEMO_POLICY")
        self.assertEqual(policy["requested_risk_percent"], 0.0)
        self.assertEqual(policy["effective_risk_percent"], 0.5)
        self.assertEqual(policy["maximum_risk_percent"], 0.5)

    def test_demo_policy_uses_persisted_risk_ceiling_and_cannot_be_raised_by_ea(self):
        DecisionThresholdConfig.objects.create(
            profile="demo", values={"risk_management": {"maximum_risk_percent": 0.75}}
        )
        pipeline = _build_pipeline(demo_mode=True)
        market = {"risk_percent": 0.1}

        policy = _apply_demo_risk_policy(market, pipeline, demo_mode=True)

        self.assertEqual(market["risk_percent"], 0.75)
        self.assertEqual(policy["effective_risk_percent"], 0.75)

    def test_live_account_does_not_accept_client_risk_percent(self):
        pipeline = _build_pipeline(demo_mode=False)
        market = {"risk_percent": 0.20882145}

        policy = _apply_demo_risk_policy(market, pipeline, demo_mode=False)

        self.assertEqual(market["risk_percent"], 0.0)
        self.assertEqual(policy["source"], "SERVER_POLICY")
        self.assertEqual(policy["effective_risk_percent"], 0.0)

    def test_demo_budget_can_size_minimum_lot_without_rounding_up(self):
        pipeline = _build_pipeline(demo_mode=True)
        market = {
            "risk_percent": 0.20882145,
            "account_balance": 2394.39,
            "stop_loss_points": 588.2,
            "point_value_per_lot": 1.0,
            "volume_step": 0.01,
            "min_volume": 0.01,
            "max_volume": 100.0,
        }

        _apply_demo_risk_policy(market, pipeline, demo_mode=True)
        verdict = pipeline.risk_manager.evaluate(market, True)

        self.assertTrue(verdict["approved"], verdict["explanation"])
        self.assertEqual(verdict["volume"], 0.02)
        self.assertLessEqual(verdict["estimated_risk"], verdict["risk_budget"])
        self.assertAlmostEqual(verdict["risk_budget"], 11.97195, places=5)
