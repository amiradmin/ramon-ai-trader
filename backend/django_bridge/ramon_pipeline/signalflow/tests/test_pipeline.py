from unittest import TestCase

import ramon_pipeline.signalflow as signalflow
from ramon_pipeline.signalflow import RiskManager, SignalFlowPipeline

from .test_opportunities import LONG_SETUP, make_bars, snapshot
from .test_risk_management import ACCOUNT


def market(**overrides):
    bars = make_bars(LONG_SETUP)
    close = bars[-1]["close"]
    return {**snapshot(bars), **ACCOUNT, "bid": close, "ask": close + 0.3, "point": 0.01,
            "stop_loss_points": 50, "entry_quality": 0.9, **overrides}


class PipelineWiringTests(TestCase):
    def test_public_api_is_exposed_from_the_package(self):
        for name in signalflow.__all__:
            self.assertTrue(hasattr(signalflow, name), name)
        self.assertEqual(len(signalflow.__all__), 9)

    def test_approved_run_uses_the_structural_stop_not_the_supplied_one(self):
        outcome = SignalFlowPipeline().run(market())
        risk = outcome["stages"]["risk_management"]
        self.assertTrue(outcome["approved"], outcome["reason"])
        self.assertEqual((outcome["action"], outcome["rejected_by"], outcome["reason"]), ("BUY", None, "SIGNALFLOW_APPROVED"))
        self.assertEqual(risk["stop_source"], "STRUCTURAL")
        self.assertGreater(risk["stop_points"], 100)  # not the 50 point value supplied by the caller
        self.assertLess(risk["stop_price"], outcome["stages"]["opportunities"]["setup"]["invalidation_price"])
        self.assertFalse(outcome["execution_enabled"])
        self.assertEqual(outcome["contract_version"], "2")

    def test_callers_without_a_point_keep_using_their_supplied_stop(self):
        snapshot_ = market()
        del snapshot_["bid"], snapshot_["ask"], snapshot_["point"]  # no quote at all, like the current endpoint
        snapshot_["spread_points"] = 20
        outcome = SignalFlowPipeline().run(snapshot_)
        risk = outcome["stages"]["risk_management"]
        self.assertTrue(outcome["approved"], outcome["reason"])
        self.assertEqual((risk["stop_source"], risk["stop_points"]), ("SUPPLIED", 50.0))
        self.assertIn("using the supplied stop_loss_points", risk["checks"][0]["detail"])

    def test_without_a_point_or_a_stop_the_trade_is_refused(self):
        snapshot_ = market()
        del snapshot_["bid"], snapshot_["ask"], snapshot_["point"], snapshot_["stop_loss_points"]
        snapshot_["spread_points"] = 20
        outcome = SignalFlowPipeline().run(snapshot_)
        self.assertEqual((outcome["rejected_by"], outcome["reason"]), ("risk_management", "MISSING_OR_INVALID_RISK_INPUT"))

    def test_a_partial_quote_is_rejected_by_entry_timing_not_silently_ignored(self):
        snapshot_ = market()
        del snapshot_["point"]
        outcome = SignalFlowPipeline().run(snapshot_)
        self.assertEqual((outcome["rejected_by"], outcome["reason"]), ("entry_timing", "INVALID_QUOTE"))

    def test_rejected_by_names_the_first_stage_that_said_no(self):
        pipeline = SignalFlowPipeline()
        no_setup = pipeline.run({"trend": "HOLD"})
        self.assertEqual((no_setup["rejected_by"], no_setup["reason"]), ("opportunities", "NO_VALID_DIRECTION"))
        wide = pipeline.run(market(ask=market()["bid"] + 0.9))
        self.assertEqual((wide["rejected_by"], wide["action"]), ("entry_timing", "HOLD"))
        self.assertEqual(wide["stages"]["risk_management"]["reason"], "UPSTREAM_STAGE_REJECTED")
        poor = pipeline.run(market(account_balance=50))
        self.assertEqual((poor["rejected_by"], poor["reason"]), ("risk_management", "RISK_BUDGET_BELOW_MINIMUM_VOLUME"))
        self.assertEqual(poor["action"], "HOLD")

    def test_risk_managers_with_the_original_signature_still_plug_in(self):
        class LegacyRisk:
            def __init__(self):
                self.calls = []

            def evaluate(self, market, upstream_approved):
                self.calls.append(upstream_approved)
                return {"approved": upstream_approved, "reason": "LEGACY_RISK", "volume": 0.01}

        legacy = LegacyRisk()
        outcome = SignalFlowPipeline(risk_manager=legacy).run(market())
        self.assertEqual(legacy.calls, [True])
        self.assertTrue(outcome["approved"])

    def test_the_default_risk_manager_receives_stage_context(self):
        seen = {}

        class Spy(RiskManager):
            def evaluate(self, market, upstream_approved, *, opportunity=None, entry=None):
                seen.update(opportunity=opportunity, entry=entry)
                return super().evaluate(market, upstream_approved, opportunity=opportunity, entry=entry)

        SignalFlowPipeline(risk_manager=Spy()).run(market())
        self.assertTrue(seen["opportunity"]["approved"])
        self.assertTrue(seen["entry"]["approved"])

    def test_the_old_endpoint_style_snapshot_is_unchanged(self):
        outcome = SignalFlowPipeline().run({**ACCOUNT, "stop_loss_points": 300, "trend": "SELL", "setup_score": 0.9,
                                            "entry_quality": 0.8, "spread_points": 20})
        self.assertEqual((outcome["action"], outcome["approved"]), ("SELL", True))
        self.assertEqual(outcome["stages"]["risk_management"]["volume"], 0.16)
