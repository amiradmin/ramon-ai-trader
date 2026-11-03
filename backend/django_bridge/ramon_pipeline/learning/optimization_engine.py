"""Offline candidate comparison only; this module cannot edit live strategy settings."""
from __future__ import annotations

from typing import Any, Mapping


class OptimizationEngine:
    """Compare completed out-of-sample evidence; never writes parameters or authorizes orders."""

    def evaluate_candidate(
        self,
        *,
        baseline: Mapping[str, Any],
        candidate: Mapping[str, Any],
        min_trades: int = 100,
    ) -> dict[str, Any]:
        if min_trades < 30:
            raise ValueError("min_trades must be at least 30")
        required = ("trades", "expectancy", "max_drawdown")
        if any(key not in baseline or key not in candidate for key in required):
            return {"status": "INSUFFICIENT_EVIDENCE", "eligible_for_review": False, "auto_apply": False}
        if int(baseline["trades"]) < min_trades or int(candidate["trades"]) < min_trades:
            return {"status": "INSUFFICIENT_EVIDENCE", "eligible_for_review": False, "auto_apply": False}
        improves_expectancy = float(candidate["expectancy"]) > float(baseline["expectancy"])
        drawdown_not_worse = float(candidate["max_drawdown"]) <= float(baseline["max_drawdown"])
        eligible = improves_expectancy and drawdown_not_worse
        return {
            "status": "ELIGIBLE_FOR_HUMAN_REVIEW" if eligible else "REJECTED_BY_EVIDENCE",
            "eligible_for_review": eligible,
            "auto_apply": False,
            "live_parameters_changed": False,
            "checks": {
                "minimum_samples": True,
                "expectancy_improved": improves_expectancy,
                "max_drawdown_not_worse": drawdown_not_worse,
            },
            "note": "Requires a reproducible out-of-sample backtest with realistic execution costs; this result never deploys parameters.",
        }
