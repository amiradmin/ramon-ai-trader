"""Orchestrate pure learning analyzers over the existing SignalFlow outcome store."""
from __future__ import annotations

from typing import Any

from ramon_pipeline.signalflow.learning import Learning
from .decision_analyzer import DecisionAnalyzer
from .entry_exit_analyzer import EntryExitAnalyzer
from .mistake_detector import MistakeDetector
from .models import AnalysisReport
from .performance_analyzer import PerformanceAnalyzer
from .regime_analyzer import RegimeAnalyzer
from .strategy_evaluator import StrategyEvaluator


class LearningEngine:
    def __init__(self, store: Any) -> None:
        self.learning = Learning(store=store)
        self.store = store

    def collect(self, outcome: Any) -> dict[str, Any]:
        return self.learning.observe(outcome)

    def analyze(self, *, as_of: Any = None, min_samples: int = 30) -> AnalysisReport:
        records, _ = self.learning._records(as_of)
        review = MistakeDetector(self.learning).analyze(as_of=as_of, min_samples=min_samples)
        return {
            "mode": "ASSISTED",
            "advisory": True,
            "auto_apply": False,
            "strategy_mutation_enabled": False,
            "performance": PerformanceAnalyzer(self.learning).analyze(as_of=as_of),
            "decisions": DecisionAnalyzer().analyze(records),
            "entry_exit": EntryExitAnalyzer().analyze(records),
            "regimes": RegimeAnalyzer(self.learning).analyze(as_of=as_of),
            "mistakes": review,
            "strategies": StrategyEvaluator(min_samples=min_samples).analyze(records),
        }

    def summary(self, as_of: Any = None) -> dict[str, Any]:
        return self.learning.summary(as_of=as_of)

    def review(self, as_of: Any = None, min_samples: int = 30) -> dict[str, Any]:
        return self.learning.review(as_of=as_of, min_samples=min_samples)

    def observe(self, outcome: Any) -> dict[str, Any]:
        return self.collect(outcome)
