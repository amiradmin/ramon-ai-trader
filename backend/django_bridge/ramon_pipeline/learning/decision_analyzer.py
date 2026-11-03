"""Decision attribution coverage; it never infers a missing decision link."""
from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Mapping


class DecisionAnalyzer:
    def analyze(self, records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
        rows = list(records)
        linked = [row for row in rows if (row.get("tags") or {}).get("decision_id")]
        actions = Counter(
            str((row.get("tags") or {}).get("side", "UNKNOWN")).upper()
            for row in linked
        )
        return {
            "outcomes": len(rows),
            "linked_outcomes": len(linked),
            "unlinked_outcomes": len(rows) - len(linked),
            "link_rate": round(len(linked) / len(rows), 4) if rows else None,
            "linked_by_side": dict(sorted(actions.items())),
            "missing_links_are_not_inferred": True,
        }
