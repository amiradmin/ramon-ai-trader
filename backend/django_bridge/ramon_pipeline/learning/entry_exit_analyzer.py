"""Entry/exit diagnostics that preserve missing broker metrics as unavailable."""
from __future__ import annotations

import math
from typing import Any, Iterable, Mapping

_METRICS = ("mae", "mfe", "entry_efficiency", "exit_efficiency", "spread_points", "slippage_points")


class EntryExitAnalyzer:
    def analyze(self, records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
        rows = list(records)
        metrics: dict[str, Any] = {}
        for name in _METRICS:
            values = []
            for row in rows:
                value = (row.get("execution_metrics") or {}).get(name)
                if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                    values.append(float(value))
            metrics[name] = {
                "observations": len(values),
                "unavailable": len(rows) - len(values),
                "average": round(sum(values) / len(values), 6) if values else None,
            }
        return {"sample_trades": len(rows), "metrics": metrics}
