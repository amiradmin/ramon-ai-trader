"""Strategy-profile comparison with minimum-sample safeguards."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Mapping

from ramon_pipeline.signalflow.learning import Learning


class StrategyEvaluator:
    def __init__(self, *, min_samples: int = 30) -> None:
        if min_samples < 10:
            raise ValueError("min_samples must be at least 10")
        self.min_samples = min_samples

    def analyze(self, records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
        groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for row in records:
            profile = (row.get("tags") or {}).get("strategy_profile")
            if profile:
                groups[str(profile)].append(row)
        result: dict[str, Any] = {}
        for profile, rows in sorted(groups.items()):
            if len(rows) < self.min_samples:
                result[profile] = {"trades": len(rows), "sufficient_data": False, "metrics": None}
                continue
            stats = Learning._stats(rows)
            result[profile] = {"trades": len(rows), "sufficient_data": True, "metrics": stats}
        return {"minimum_samples": self.min_samples, "profiles": result, "ranking": None}
