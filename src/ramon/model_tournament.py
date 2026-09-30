"""Offline chronological challengers. No change to the active model or EA."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import math

from .compare import MomentumBaseline
from .core import Settings
from .history import load_bars
from .model import ChronosForecaster
from .replay import replay


def tournament(bars, spreads, models: dict, *, start: int, point: float, cost_r: float) -> dict:
    if (start < 256 or len(bars) - start < 100 or len(models) < 2
        or not math.isfinite(point) or not math.isfinite(cost_r) or not 0 < point or not 0 <= cost_r):
        raise ValueError("need two models, 256 context bars, 100 holdout bars and valid costs")
    if any(b.time - a.time != 900 for a, b in zip(bars, bars[1:])):
        raise ValueError("use a contiguous M15 window; missing/weekend bars cannot count as continuous forecast horizons")
    kwargs = dict(point=point, settings=Settings(), start=start, stride=1,
                  require_recorded_spreads=True, roundtrip_cost_r=cost_r)
    # Same completed-bar holdout and execution assumptions, separate one-position paths.
    results = {name: asdict(replay(bars, spreads, model, **kwargs)) for name, model in models.items()}
    return {"holdout_start_mt5": bars[start].time, "holdout_end_mt5": bars[-1].time,
            "results": results, "promotion": False,
            "limitations": ["M15 conservative first-touch approximation, not actual EA exits or live P/L.",
                            "Different models can enter at different times; forecasts use historical context only.",
                            "Candidate training must end before the declared holdout; no automatic selection."]}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", required=True)
    p.add_argument("--symbol", default="XAUUSD_l")
    p.add_argument("--baseline", default="autogluon/chronos-2-small")
    p.add_argument("--challenger", required=True, help="Chronos-compatible checkpoint/model ID")
    p.add_argument("--training-end-mt5", type=int, required=True, help="Candidate training cutoff in raw MT5 bar clock")
    p.add_argument("--holdout-start-mt5", type=int, required=True)
    p.add_argument("--holdout-end-mt5", type=int, required=True)
    p.add_argument("--cost-r", type=float, required=True, help="Explicit estimated commission/fee/slippage cost per initial R; spread already included")
    p.add_argument("--point", type=float, default=.01)
    p.add_argument("--device", default="cpu")
    a = p.parse_args()
    if a.training_end_mt5 >= a.holdout_start_mt5:
        p.error("candidate training must end strictly before the holdout")
    bars, spreads = load_bars(a.db, a.symbol)
    selected = [(b, s) for b, s in zip(bars, spreads) if b.time <= a.holdout_end_mt5]
    start = next((i for i, (b, _) in enumerate(selected) if b.time >= a.holdout_start_mt5), len(selected))
    # Use exactly 256 historical context bars before the holdout.
    selected = selected[max(0, start - 256):]
    start = min(start, 256)
    if len(selected) - start < 100 or any(s <= 0 for _, s in selected[start:]):
        p.error("need >=100 holdout bars with recorded spreads")
    models = {"baseline": ChronosForecaster(a.baseline, a.device),
              "challenger": ChronosForecaster(a.challenger, a.device), "momentum_control": MomentumBaseline()}
    result = tournament([b for b, _ in selected], [s for _, s in selected], models,
                        start=start, point=a.point, cost_r=a.cost_r)
    result["model_identity"] = {name: {"id": getattr(m, "model_id", name), "revision": getattr(m, "revision", None)}
                                for name, m in models.items()}
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
