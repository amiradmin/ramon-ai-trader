"""Overall performance metrics backed by the established SignalFlow statistics."""
from __future__ import annotations

from typing import Any

from ramon_pipeline.signalflow.learning import Learning


class PerformanceAnalyzer:
    def __init__(self, learning: Learning) -> None:
        self.learning = learning

    def analyze(self, *, as_of: Any = None) -> dict[str, Any]:
        return self.learning.summary(as_of=as_of)
