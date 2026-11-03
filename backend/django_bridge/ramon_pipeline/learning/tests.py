from django.test import SimpleTestCase

from ramon_pipeline.learning import (
    DecisionAnalyzer, EntryExitAnalyzer, LearningEngine, OptimizationEngine,
    RecommendationEngine, StrategyEvaluator,
)
from ramon_pipeline.signalflow.learning import InMemoryOutcomeStore


class LearningPackageTests(SimpleTestCase):
    def test_engine_exposes_separated_analysis_without_mutation(self):
        store = InMemoryOutcomeStore()
        engine = LearningEngine(store)
        engine.collect({
            "trade_id": "trade-1", "realized_pnl": 12, "risk_amount": 10,
            "closed_at": 1_800_000_000, "decision_id": "decision-1",
            "side": "BUY", "regime": "BULLISH", "strategy_profile": "BALANCED",
        })
        report = engine.analyze(min_samples=10)
        self.assertEqual(report["performance"]["overall"]["trades"], 1)
        self.assertEqual(report["decisions"]["linked_outcomes"], 1)
        self.assertFalse(report["auto_apply"])
        self.assertFalse(report["strategy_mutation_enabled"])

    def test_missing_execution_metrics_remain_unavailable(self):
        result = EntryExitAnalyzer().analyze([{"execution_metrics": {}}])
        self.assertEqual(result["metrics"]["mae"]["observations"], 0)
        self.assertEqual(result["metrics"]["mae"]["unavailable"], 1)
        self.assertIsNone(result["metrics"]["mae"]["average"])

    def test_decision_analyzer_does_not_infer_missing_links(self):
        result = DecisionAnalyzer().analyze([{"tags": {}}, {"tags": {"decision_id": "d1"}}])
        self.assertEqual(result["unlinked_outcomes"], 1)
        self.assertTrue(result["missing_links_are_not_inferred"])

    def test_strategy_profiles_need_minimum_samples(self):
        result = StrategyEvaluator(min_samples=10).analyze([{"tags": {"strategy_profile": "BALANCED"}}])
        self.assertFalse(result["profiles"]["BALANCED"]["sufficient_data"])

    def test_optimizer_only_marks_candidates_for_review(self):
        engine = OptimizationEngine()
        result = engine.evaluate_candidate(
            baseline={"trades": 120, "expectancy": 0.1, "max_drawdown": 20},
            candidate={"trades": 120, "expectancy": 0.2, "max_drawdown": 18},
        )
        self.assertTrue(result["eligible_for_review"])
        self.assertFalse(result["auto_apply"])
        self.assertFalse(result["live_parameters_changed"])

    def test_recommendations_are_advisory(self):
        result = RecommendationEngine().generate({
            "sufficient_data": True,
            "findings": [{"code": "SEGMENT_UNDERPERFORMS", "evidence": {"trades": 40}}],
        })
        self.assertFalse(result[0]["auto_apply"])
