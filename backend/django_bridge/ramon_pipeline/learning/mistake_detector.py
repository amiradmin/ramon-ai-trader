"""Conservative statistical issue detection; findings remain hypotheses, not trade rules."""
from __future__ import annotations

from typing import Any

from ramon_pipeline.signalflow.learning import Learning


class MistakeDetector:
    def __init__(self, learning: Learning) -> None:
        self.learning = learning

    def analyze(self, *, as_of: Any = None, min_samples: int = 30) -> dict[str, Any]:
        return self.learning.review(as_of=as_of, min_samples=min_samples)
