"""Outcome validation facade; persistence is supplied through the OutcomeStore protocol."""
from __future__ import annotations

from typing import Any, Mapping

from ramon_pipeline.signalflow.learning import Learning, OutcomeStore


class OutcomeCollector:
    """Validate and optionally persist broker outcomes without changing trading state."""

    def __init__(self, learning: Learning | None = None) -> None:
        self.learning = learning or Learning()

    def collect(self, outcome: Mapping[str, Any] | None) -> dict[str, Any]:
        return self.learning.observe(outcome)

    @classmethod
    def with_store(cls, store: OutcomeStore) -> "OutcomeCollector":
        return cls(Learning(store=store))
