from django.test import TestCase, override_settings

from .decision import _apply_demo_risk_policy, _build_pipeline


class DemoRiskPolicyTests(TestCase):
    @override_settings(RAMON_DEMO_RISK_PERCENT=0.5)
    def test_demo_uses_server_owned_half_percent_instead_of_ea_five_dollar_cap(self):
        pipeline = _build_pipeline(demo_mode=True)
        market = {"risk_percent": 0.20882145}

        policy = _apply_demo_risk_policy(market, pipeline, demo_mode=True)

        self.assertEqual(market["risk_percent"], 0.5)
        self.assertEqual(policy["source"], "SERVER_DEMO_POLICY")
        self.assertEqual(policy["requested_risk_percent"], 0.0)
        self.assertEqual(policy["effective_risk_percent"], 0.5)
        self.assertEqual(policy["maximum_risk_percent"], 0.5)

    @override_settings(RAMON_DEMO_RISK_PERCENT=5.0)
    def test_demo_policy_cannot_exceed_server_risk_manager_ceiling(self):
        pipeline = _build_pipeline(demo_mode=True)
        market = {"risk_percent": 0.1}

        policy = _apply_demo_risk_policy(market, pipeline, demo_mode=True)

        self.assertEqual(market["risk_percent"], 0.5)
        self.assertEqual(policy["effective_risk_percent"], 0.5)

    @override_settings(RAMON_DEMO_RISK_PERCENT=0.5)
    def test_live_account_does_not_accept_client_risk_percent(self):
        pipeline = _build_pipeline(demo_mode=False)
        market = {"risk_percent": 0.20882145}

        policy = _apply_demo_risk_policy(market, pipeline, demo_mode=False)

        self.assertEqual(market["risk_percent"], 0.0)
        self.assertEqual(policy["source"], "SERVER_POLICY")
        self.assertEqual(policy["effective_risk_percent"], 0.0)

    @override_settings(RAMON_DEMO_RISK_PERCENT=0.5)
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
