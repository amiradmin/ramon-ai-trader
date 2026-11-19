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

        self.assertEqual(market["risk_percent"], 0.5)
        self.assertEqual(policy["effective_risk_percent"], 0.5)
        self.assertEqual(policy["maximum_risk_percent"], 0.5)

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

    def test_standard_profile_persisted_risk_cannot_exceed_safety_cap(self):
        DecisionThresholdConfig.objects.create(
            profile="standard", values={"risk_management": {"maximum_risk_percent": 1.6}}
        )
        pipeline = _build_pipeline(demo_mode=False)
        self.assertEqual(pipeline.risk_manager.maximum_risk_percent, 0.5)

    def test_standard_demo_account_uses_same_safety_cap(self):
        from django.test import override_settings
        with override_settings(RAMON_DEMO_PROFILE="standard"):
            pipeline = _build_pipeline(demo_mode=True)
            self.assertEqual(pipeline.risk_manager.maximum_risk_percent, 0.5)


class FrozenPolicyParityTests(TestCase):
    def test_policy_fingerprint_changes_when_short_stop_setting_changes(self):
        from django.test import override_settings
        from .policy_snapshot import effective_profiles, fingerprint
        with override_settings(RAMON_SHORT_STOP_SPREAD_ADJUST=False):
            without = effective_profiles()
        with override_settings(RAMON_SHORT_STOP_SPREAD_ADJUST=True):
            with_adjustment = effective_profiles()
        self.assertNotEqual(fingerprint(without), fingerprint(with_adjustment))
        self.assertFalse(without["standard"]["short_stop_spread_adjust"])
        self.assertTrue(with_adjustment["standard"]["short_stop_spread_adjust"])

    def test_strict_audit_rejects_different_frozen_replay_policy(self):
        import io
        import json
        import tempfile
        from django.core.management import call_command
        from django.core.management.base import CommandError
        from .policy_snapshot import effective_profiles
        snapshot = effective_profiles()
        snapshot["standard"]["min_stop_atr"] += 0.1
        with tempfile.NamedTemporaryFile("w+", suffix=".json") as file:
            json.dump({"profiles": snapshot}, file)
            file.flush()
            with self.assertRaises(CommandError):
                call_command("audit_trading_readiness", source="test-source",
                             expected_config=file.name, strict=True, stdout=io.StringIO())

    def test_strict_audit_accepts_identical_frozen_policy_with_clean_data(self):
        import io
        import json
        import tempfile
        from django.core.management import call_command
        from .policy_snapshot import effective_profiles
        with tempfile.NamedTemporaryFile("w+", suffix=".json") as file:
            json.dump({"profiles": effective_profiles()}, file)
            file.flush()
            output = io.StringIO()
            call_command("audit_trading_readiness", source="test-source",
                         expected_config=file.name, strict=True, stdout=output)
            report = json.loads(output.getvalue())
            self.assertEqual(report["parity"]["state"], "MATCH")
