"""Convert statistical findings into deduplicatable human-review hypotheses."""
from __future__ import annotations

from typing import Any, Mapping


class RecommendationEngine:
    def generate(self, review: Mapping[str, Any]) -> list[dict[str, Any]]:
        if not review.get("sufficient_data"):
            return []
        recommendations = []
        for finding in review.get("findings", []):
            if not isinstance(finding, Mapping):
                continue
            recommendations.append({
                "finding_code": str(finding.get("code") or "UNKNOWN"),
                "severity": str(finding.get("severity") or "INFO"),
                "title": str(finding.get("code") or "UNKNOWN").replace("_", " ").title(),
                "detail": str(finding.get("detail") or ""),
                "evidence": dict(finding.get("evidence") or {}),
                "advisory": True,
                "auto_apply": False,
                "next_step": "Review and validate as an out-of-sample backtest hypothesis.",
            })
        return recommendations
