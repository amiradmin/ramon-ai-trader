"""Regression checks: research tuning must not affect the live pipeline."""
from django.test import TestCase

from .decision import _build_pipeline
from .replay_backtest import _research_pipeline
from .management.commands.research_trend_pullback_sweep import PROFILES


class TrendPullbackResearchIsolationTests(TestCase):
    def test_research_override_does_not_modify_live_defaults(self):
        before = _build_pipeline(demo_mode=False, strategy_setup="TREND_PULLBACK")
        original = before.opportunities.config
        candidate = _research_pipeline(
            "TREND_PULLBACK", research_overrides=PROFILES["combined_experiment"]
        )
        self.assertEqual(candidate.opportunities.config.min_impulse_atr, 1.25)
        self.assertEqual(candidate.opportunities.config.min_retracement, 0.15)
        self.assertEqual(candidate.opportunities.config.max_retracement, 0.88)
        self.assertEqual(candidate.opportunities.config.regime_lookback, 5)
        self.assertEqual(candidate.opportunities.config.atr_ratio_min, 0.50)
        after = _build_pipeline(demo_mode=False, strategy_setup="TREND_PULLBACK")
        self.assertEqual(after.opportunities.config, original)
        self.assertLessEqual(candidate.risk_manager.maximum_risk_percent, 0.5)

    def test_unsupported_or_invalid_research_settings_fail_closed(self):
        for overrides in (
            {"maximum_risk_percent": 5},
            {"min_retracement": 0.90, "max_retracement": 0.80},
            {"atr_ratio_min": -1},
            {"regime_lookback": 4.5},
            {"min_impulse_atr": float("nan")},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                _research_pipeline("TREND_PULLBACK", research_overrides=overrides)

    def test_only_trend_pullback_can_use_structural_research_overrides(self):
        with self.assertRaises(ValueError):
            _research_pipeline("BREAKOUT_RETEST", research_overrides={"min_impulse_atr": 1.25})
