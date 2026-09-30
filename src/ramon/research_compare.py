"""Compare price-only and past-covariate Chronos without changing live artifacts."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import math
from pathlib import Path

from .bundles import atomic_json
from .compare import compare
from .core import Settings
from .covariates import CovariateForecaster
from .history import load_bars
from .model import ChronosForecaster, model_name
from .replay import replay


def research_compare(db, model, *, symbol="XAUUSD_l", point=.01, stride=4, cost_r=None):
    if cost_r is not None and (not math.isfinite(cost_r) or cost_r < 0):
        raise ValueError("cost-r must be finite and nonnegative")
    report = compare(db, model, symbol=symbol, point=point, stride=stride, cost_r=cost_r)
    bars, spreads = load_bars(db, symbol)
    enriched = CovariateForecaster(model)
    result = replay(bars, spreads, model, symbol=symbol, point=point,
                    start=max(256, len(bars)*4//5), stride=stride, settings=Settings(),
                    require_recorded_spreads=True, roundtrip_cost_r=report["cost"]["roundtrip_r"] or 0,
                    model_for_market=enriched.for_market)
    report["results"]["chronos_past_covariates"] = asdict(result)
    report.update(mode="offline_research", live_execution_effect="NONE", promotion_allowed=False,
                  chronos_model=model.model_id, chronos_revision=model.revision,
                  covariates=["range", "body", "return", "atr14"],
                  unavailable_inputs=["tick_volume", "historical_news", "verified_UTC_sessions"])
    report["interpretation"] = (
        "insufficient traded samples; no winner selected"
        if min(r["buys"]+r["sells"] for r in report["results"].values()) < 20 else
        "descriptive comparison only; validate on a fresh period before selecting")
    report["limitations"].extend([
        "The same checkpoint is shared; historical OHLC covariates are never supplied into the future.",
        "This replay omits the live EA TP stages, trailing/profit protection and cooldown policy.",
        "Checkpoint pretraining overlap with historical data is unknown; a fresh forward period remains necessary.",
        "R-normalized replay results do not model broker minimum-lot USD risk or account equity.",
    ])
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="/data/ramon_history.sqlite3")
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--model", default="autogluon/chronos-2-small")
    parser.add_argument("--active-model-file", default="/checkpoints/active_model.txt")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--stride", type=int, default=4)
    parser.add_argument("--cost-r", type=float)
    parser.add_argument("--out", default="/data/research/chronos-covariates.json")
    args = parser.parse_args()
    bars, spreads = load_bars(args.db, args.symbol)
    if len(bars) < 600 or any(v <= 0 for v in spreads[max(256, len(bars)*4//5):]):
        parser.error("need >=600 completed M15 bars and recorded spreads throughout the final 20%; import MT5 history first")
    if args.stride < 1 or (args.cost_r is not None and (not math.isfinite(args.cost_r) or args.cost_r < 0)):
        parser.error("invalid stride or cost-r")
    checkpoint = Path(args.active_model_file)
    model_id = checkpoint.read_text().strip() if checkpoint.exists() else args.model
    model = ChronosForecaster(model_name(model_id), args.device)
    report = research_compare(args.db, model, symbol=args.symbol, stride=args.stride, cost_r=args.cost_r)
    atomic_json(Path(args.out), report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
