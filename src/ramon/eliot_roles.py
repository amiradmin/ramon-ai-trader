"""Offline Eliot M5 learned roles with chronological evaluation."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

from .core import atr14
from .eliot_research import Row, load_m5
from .ensemble import BinaryLogisticModel, regime_features, train_binary_logistic


FEATURES = (
    "side_sell", "ret_1_atr_aligned", "ret_4_atr_aligned",
    "ret_12_atr_aligned", "range_4_atr", "range_12_atr",
    "body_efficiency_12", "atr_pct", "spread_atr",
)
HORIZON = 3
CONTEXT = 64


def eligible(rows: list[Row], index: int) -> bool:
    if index < CONTEXT - 1 or index + HORIZON >= len(rows):
        return False
    return (all(rows[j].bar.time - rows[j - 1].bar.time == 300
                for j in range(index - CONTEXT + 2, index + HORIZON + 1))
            and rows[index].spread <= .50)


def features(rows: list[Row], index: int, side: int) -> dict[str, float]:
    bars = [r.bar for r in rows[index - CONTEXT + 1:index + 1]]
    regime = regime_features(bars)
    return {
        "side_sell": float(side == -1),
        "ret_1_atr_aligned": side * regime["ret_1_atr"],
        "ret_4_atr_aligned": side * regime["ret_4_atr"],
        "ret_12_atr_aligned": side * regime["ret_12_atr"],
        "range_4_atr": regime["range_4_atr"],
        "range_12_atr": regime["range_12_atr"],
        "body_efficiency_12": regime["body_efficiency_12"],
        "atr_pct": regime["atr_pct"],
        "spread_atr": rows[index].spread / max(atr14(bars), .01),
    }


def outcome(rows: list[Row], index: int, side: int,
            units_per_price: float) -> tuple[str, float]:
    """Same next-open fill and stop-first M5 proxy as the baseline replay."""
    target = 5 / units_per_price
    stop = 6 / units_per_price
    first = rows[index + 1]
    entry = first.bar.open + (first.spread if side == 1 else 0)
    take, loss = entry + side * target, entry - side * stop
    for row in rows[index + 1:index + HORIZON + 1]:
        high = row.bar.high + (row.spread if side == -1 else 0)
        low = row.bar.low + (row.spread if side == -1 else 0)
        if (low <= loss if side == 1 else high >= loss):
            return "SL", -6.0
        if (high >= take if side == 1 else low <= take):
            return "TP", 5.0
    last = rows[index + HORIZON]
    exit_price = last.bar.close + (last.spread if side == -1 else 0)
    return "TIME", side * (exit_price - entry) * units_per_price


def train_roles(rows: list[Row], *, end: int, units_per_price: float,
                symbol: str = "XAUUSD_l"
                ) -> dict[str, BinaryLogisticModel]:
    regime_x: list[dict[str, float]] = []
    regime_y: list[int] = []
    direction_x: list[dict[str, float]] = []
    direction_y: list[int] = []
    entry_x: list[dict[str, float]] = []
    entry_y: list[int] = []
    # End-exclusive: no training target can touch validation or test bars.
    for index in range(CONTEXT - 1, end - HORIZON):
        if not eligible(rows, index):
            continue
        buy = features(rows, index, 1)
        sell = features(rows, index, -1)
        buy_result = outcome(rows, index, 1, units_per_price)[1]
        sell_result = outcome(rows, index, -1, units_per_price)[1]
        regime_x.append(buy)
        regime_y.append(int(max(buy_result, sell_result) > 0))
        direction_x.append(buy)
        direction_y.append(int(buy_result > sell_result))
        entry_x.extend((buy, sell))
        entry_y.extend((int(buy_result > 0), int(sell_result > 0)))
    meta = {"symbol": symbol, "timeframe": "M5", "train_end_exclusive": end,
            "target_units": 5, "stop_units": 6, "horizon_bars": HORIZON}
    return {
        "regime": train_binary_logistic(regime_x, regime_y, FEATURES,
                                         steps=200, metadata={**meta, "role": "regime"}),
        "direction": train_binary_logistic(direction_x, direction_y, FEATURES,
                                            steps=200, metadata={**meta, "role": "direction"}),
        "entry": train_binary_logistic(entry_x, entry_y, FEATURES,
                                        steps=200, metadata={**meta, "role": "entry"}),
    }


def evaluate(rows: list[Row], chronos: object,
             roles: dict[str, BinaryLogisticModel], *, start: int, end: int,
             units_per_price: float) -> dict[str, object]:
    count = 0
    baseline = Counter()
    filtered = Counter()
    baseline_pnl = filtered_pnl = 0.0
    baseline_next = filtered_next = start
    for index in range(start, min(end, len(rows) - HORIZON)):
        if not eligible(rows, index):
            continue
        closes = [r.bar.close for r in rows[index - CONTEXT + 1:index + 1]]
        forecast = chronos.forecast(closes, HORIZON)
        count += 1
        delta = forecast.median - closes[-1]
        if abs(delta) < max(1.0, 2 * rows[index].spread):
            continue
        side = 1 if delta > 0 else -1
        if index >= baseline_next:
            result, gain = outcome(rows, index, side, units_per_price)
            baseline[result] += 1
            baseline_pnl += gain
            baseline_next = index + HORIZON
        signals = features(rows, index, side)
        regime_p = roles["regime"].predict_proba(signals)
        direction_p = roles["direction"].predict_proba(features(rows, index, 1))
        entry_p = roles["entry"].predict_proba(signals)
        agrees = direction_p >= .55 if side == 1 else direction_p <= .45
        if index >= filtered_next and regime_p >= .55 and agrees and entry_p >= .60:
            result, gain = outcome(rows, index, side, units_per_price)
            filtered[result] += 1
            filtered_pnl += gain
            filtered_next = index + HORIZON
    return {
        "eligible_forecasts": count,
        "chronos_only": {"entries": sum(baseline.values()), "outcomes": dict(baseline),
                         "net_account_units": round(baseline_pnl, 3)},
        "chronos_with_m5_roles": {"entries": sum(filtered.values()),
                                  "outcomes": dict(filtered),
                                  "net_account_units": round(filtered_pnl, 3)},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Eliot M5 trained-role Chronos research")
    parser.add_argument("--db", default="/data/ramon_history.sqlite3")
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--model", default="autogluon/chronos-2-small")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--units-per-price", type=float, required=True)
    parser.add_argument("--models-dir", default="/data/eliot_models")
    args = parser.parse_args()
    if args.units_per_price <= 0:
        parser.error("--units-per-price must be positive")
    rows = load_m5(args.db, args.symbol, .01)
    train_end = len(rows) * 7 // 10
    validation_end = len(rows) * 8 // 10
    roles = train_roles(rows, end=train_end, units_per_price=args.units_per_price,
                        symbol=args.symbol)
    target_dir = Path(args.models_dir)
    for name, role in roles.items():
        role.save(target_dir / f"{name}_m5.json")
    from .model import ChronosForecaster
    chronos = ChronosForecaster(args.model, args.device)
    result = {
        "train_end": train_end, "validation_end": validation_end,
        "test_end": len(rows), "trained_models": str(target_dir),
        "validation": evaluate(rows, chronos, roles, start=train_end,
                               end=validation_end, units_per_price=args.units_per_price),
        "test": evaluate(rows, chronos, roles, start=validation_end,
                         end=len(rows), units_per_price=args.units_per_price),
        "limitations": "Research only; test period was previously inspected for Chronos-only strategy. "
                       "M5 spread proxy, stop-first candles, no slippage/fees, no live EA. "
                       "Role labels use bar outcomes and exclude news; no future bars in role inputs.",
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
