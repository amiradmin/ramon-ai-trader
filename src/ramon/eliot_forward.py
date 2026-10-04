"""Freeze Eliot's Chronos-aligned M5 roles and test only later bars."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

from .eliot_research import Row, load_m5
from .eliot_roles import CONTEXT, HORIZON, eligible, features, outcome
from .ensemble import BinaryLogisticModel, train_binary_logistic


FEATURES = (
    "side_sell", "ret_1_atr_aligned", "ret_4_atr_aligned",
    "ret_12_atr_aligned", "range_4_atr", "range_12_atr",
    "body_efficiency_12", "atr_pct", "spread_atr",
    "forecast_move_atr", "forecast_uncertainty_atr", "forecast_slope_atr",
)
ROLE_NAMES = ("profit", "take", "stop")


def candidate(rows: list[Row], index: int, forecaster: object
              ) -> tuple[int, dict[str, float]] | None:
    from .core import atr14

    if not eligible(rows, index):
        return None
    context = rows[index - CONTEXT + 1:index + 1]
    prediction = forecaster.forecast([r.bar.close for r in context], HORIZON)
    delta = prediction.median - rows[index].bar.close
    if abs(delta) < max(1.0, rows[index].spread * 2):
        return None
    side = 1 if delta > 0 else -1
    atr = max(atr14([r.bar for r in context]), .01)
    values = features(rows, index, side)
    values.update({
        "forecast_move_atr": abs(delta) / atr,
        "forecast_uncertainty_atr": max(0.0, prediction.high - prediction.low) / atr,
        "forecast_slope_atr": (prediction.median_path[-1] - prediction.median_path[0]) / atr
        if len(prediction.median_path) == HORIZON else delta / atr,
    })
    return side, values


def samples(rows: list[Row], forecaster: object, *, start: int, end: int,
            units_per_price: float):
    """End-exclusive candidate labels; every label uses at most three later bars."""
    for index in range(max(CONTEXT - 1, start), min(end - HORIZON, len(rows) - HORIZON)):
        item = candidate(rows, index, forecaster)
        if item is None:
            continue
        side, values = item
        result, gain = outcome(rows, index, side, units_per_price)
        yield index, values, result, gain


def fit(rows: list[Row], forecaster: object, *, cutoff: int,
        units_per_price: float, symbol: str) -> tuple[dict[str, BinaryLogisticModel], dict]:
    end = next((i for i, row in enumerate(rows) if row.bar.time > cutoff), len(rows))
    if end < 2000 or rows[end - 1].bar.time != cutoff:
        raise ValueError("cutoff must match a historical closed M5 bar with >=2000 older bars")
    observed = list(samples(rows, forecaster, start=0, end=end,
                            units_per_price=units_per_price))
    if len(observed) < 100:
        raise ValueError("not enough Chronos-selected M5 training examples")
    xs = [sample[1] for sample in observed]
    labels = {
        "profit": [int(gain > 0) for _, _, _, gain in observed],
        "take": [int(result == "TP") for _, _, result, _ in observed],
        "stop": [int(result == "SL") for _, _, result, _ in observed],
    }
    meta = {"symbol": symbol, "timeframe": "M5", "cutoff_time": cutoff,
            "model": getattr(forecaster, "model_id", "unknown"),
            "model_revision": getattr(forecaster, "revision", None),
            "units_per_price": units_per_price, "context_bars": CONTEXT,
            "target_units": 5, "stop_units": 6, "horizon_bars": HORIZON}
    roles = {name: train_binary_logistic(xs, labels[name], FEATURES, steps=300,
                                         metadata={**meta, "role": name})
             for name in ROLE_NAMES}
    summary = {"cutoff_time": cutoff, "train_candidates": len(observed),
               "train_class_counts": {name: sum(values) for name, values in labels.items()},
               "model_id": meta["model"], "model_revision": meta["model_revision"],
               "units_per_price": units_per_price, "symbol": symbol,
               "note": "Training examples only; future assessment requires new completed M5 bars."}
    return roles, summary


def forward(rows: list[Row], forecaster: object,
            roles: dict[str, BinaryLogisticModel], *, cutoff: int,
            units_per_price: float) -> dict:
    newer = [i for i, row in enumerate(rows) if row.bar.time > cutoff]
    if not newer:
        return {"status": "AWAITING_NEW_M5_BARS", "candidate_signals": 0,
                "chronos_only": {"entries": 0}, "chronos_with_roles": {"entries": 0}}
    start = newer[0]
    baseline = Counter()
    filtered = Counter()
    baseline_pnl = filtered_pnl = 0.0
    baseline_next = filtered_next = start
    candidates = 0
    for index, values, result, gain in samples(rows, forecaster, start=start,
                                               end=len(rows), units_per_price=units_per_price):
        candidates += 1
        if index >= baseline_next:
            baseline[result] += 1
            baseline_pnl += gain
            baseline_next = index + HORIZON
        p = {name: roles[name].predict_proba(values) for name in ROLE_NAMES}
        # Fixed before later data is evaluated; do not select gates on future results.
        if (index >= filtered_next and p["profit"] >= .55
                and p["take"] * 5 - p["stop"] * 6 >= .5):
            filtered[result] += 1
            filtered_pnl += gain
            filtered_next = index + HORIZON
    def report(counts, pnl):
        return {"entries": sum(counts.values()), "outcomes": dict(counts),
                "net_account_units": round(pnl, 3)}
    return {"status": "EVALUATED", "new_completed_bars": len(newer),
            "candidate_signals": candidates, "chronos_only": report(baseline, baseline_pnl),
            "chronos_with_roles": report(filtered, filtered_pnl)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Freeze Eliot M5 roles for forward-only research")
    parser.add_argument("action", choices=("fit", "evaluate"))
    parser.add_argument("--db", default="/data/ramon_history.sqlite3")
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--model", default="autogluon/chronos-2-small")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--units-per-price", type=float, required=True)
    parser.add_argument("--cutoff-time", type=int,
                        help="required for fit: last completed historical M5 bar timestamp")
    parser.add_argument("--output", default="/data/eliot_forward")
    args = parser.parse_args()
    if args.units_per_price <= 0:
        parser.error("--units-per-price must be positive")
    rows = load_m5(args.db, args.symbol, .01)
    root = Path(args.output)
    manifest_path = root / "manifest.json"
    if args.action == "fit" and (args.cutoff_time is None or manifest_path.exists()):
        parser.error("fit requires --cutoff-time and a new output directory; frozen models cannot be overwritten")
    if args.action == "evaluate":
        if not manifest_path.exists():
            parser.error("run fit first: frozen model manifest is missing")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (manifest["symbol"] != args.symbol or manifest["model_id"] != args.model
                or manifest["units_per_price"] != args.units_per_price):
            parser.error("symbol, Chronos model and money conversion must match frozen fit")
        cutoff = int(manifest["cutoff_time"])
        roles = {name: BinaryLogisticModel.load(root / f"{name}.json") for name in ROLE_NAMES}
        if any(role.metadata.get("cutoff_time") != cutoff for role in roles.values()):
            parser.error("frozen model cutoff mismatch")
        if not any(row.bar.time > cutoff for row in rows):
            print(json.dumps(forward(rows, None, roles, cutoff=cutoff,
                                     units_per_price=args.units_per_price), indent=2))
            return
    from .model import ChronosForecaster
    chronos = ChronosForecaster(args.model, args.device)
    if args.action == "fit":
        roles, summary = fit(rows, chronos, cutoff=args.cutoff_time,
                             units_per_price=args.units_per_price, symbol=args.symbol)
        root.mkdir(parents=True, exist_ok=False)
        for name, role in roles.items():
            role.save(root / f"{name}.json")
        manifest_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(json.dumps(summary, indent=2))
    else:
        if manifest.get("model_revision") and chronos.revision != manifest["model_revision"]:
            parser.error("Chronos revision changed since fit; forward result would use a different model")
        print(json.dumps(forward(rows, chronos, roles, cutoff=cutoff,
                                 units_per_price=args.units_per_price), indent=2))


if __name__ == "__main__":
    main()
