from django.test import TestCase

from .decision import _build_pipeline
from .strategy_registry import STRATEGIES
from ramon_pipeline.signalflow.trading_config import load_trading_config


class IndependentSetupActivationTests(TestCase):
    def test_live_worker_activates_only_its_assigned_setup(self):
        scope = ("test-instance", "test-login", "test-server", "XAUUSD_l")
        for setup in STRATEGIES:
            with self.subTest(setup=setup):
                pipeline = _build_pipeline(strategy_setup=setup, live_scope=scope)
                detector = pipeline.opportunities
                policies = detector.trading_settings["opportunity_finder"]["setups"]
                self.assertEqual(
                    {name for name, policy in policies.items() if policy["enabled"]},
                    {setup.lower()},
                )

    def test_unscoped_standard_defaults_are_unchanged(self):
        defaults = load_trading_config()["opportunity_finder"]["setups"]
        pipeline = _build_pipeline()
        self.assertEqual(
            pipeline.opportunities.trading_settings["opportunity_finder"]["setups"],
            defaults,
        )
        for setup in STRATEGIES:
            with self.subTest(setup=setup):
                pipeline = _build_pipeline(strategy_setup=setup)
                policies = pipeline.opportunities.trading_settings["opportunity_finder"]["setups"]
                expected = {setup.lower()} if defaults[setup.lower()]["enabled"] else set()
                self.assertEqual(
                    {name for name, policy in policies.items() if policy["enabled"]},
                    expected,
                )
