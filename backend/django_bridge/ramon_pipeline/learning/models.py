"""Typed domain contracts for assisted learning; Django ORM models stay in ea_bridge.models."""
from __future__ import annotations

from typing import NotRequired, TypedDict


class OutcomeRecord(TypedDict):
    trade_id: str
    realized_pnl: float
    opened_at: NotRequired[float | None]
    closed_at: NotRequired[float | None]
    risk_amount: NotRequired[float | None]
    r_multiple: NotRequired[float | None]
    tags: NotRequired[dict[str, object]]
    execution_metrics: NotRequired[dict[str, float]]


class AnalysisReport(TypedDict):
    mode: str
    advisory: bool
    auto_apply: bool
    strategy_mutation_enabled: bool
    performance: dict[str, object]
    decisions: dict[str, object]
    entry_exit: dict[str, object]
    regimes: dict[str, object]
    mistakes: dict[str, object]
    strategies: dict[str, object]
