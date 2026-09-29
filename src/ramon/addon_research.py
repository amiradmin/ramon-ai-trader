"""Read-only add-on trade event study from actual Ramon trade times and MT5 M5 bars.

The report has minute-resolution entry/exit times but not broker fill prices.
Therefore the original entry is approximated from the first subsequent M5 open.
This study is NOT an EA backtest or a warrant to enable a second live position.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re

from .stop_feasibility import Bar, load_m15


TRADE_LINE = re.compile(
    r"^\s*(\d+) \| (?:WIN|LOSS)\s+\| (BUY|SELL)\s+\| "
    r"(\d{4}-\d\d-\d\d \d\d:\d\d) UTC -> "
    r"(\d{4}-\d\d-\d\d \d\d:\d\d) UTC \| net=\s*([+-]?\d+(?:\.\d+)?)"
    r" \| risk=\s*(\d+(?:\.\d+)?)"
)


@dataclass(frozen=True)
class Trade:
    number: int
    direction: str
    opened_broker: int
    closed_broker: int
    net_units: float
    risk_units: float


def read_trades(path: str | Path, *, broker_utc_offset: int) -> list[Trade]:
    if abs(broker_utc_offset) > 14 * 3600 or broker_utc_offset % 900:
        raise ValueError("broker UTC offset must be within 14 hours, in 15-minute steps")
    trades = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        match = TRADE_LINE.match(line)
        if not match:
            continue
        number, direction, opened, closed, net, risk = match.groups()
        to_epoch = lambda value: int(datetime.strptime(value, "%Y-%m-%d %H:%M")
                                     .replace(tzinfo=timezone.utc).timestamp())
        entry = to_epoch(opened) + broker_utc_offset
        exit_ = to_epoch(closed) + broker_utc_offset
        if exit_ > entry and float(risk) > 0:
            trades.append(Trade(int(number), direction, entry, exit_, float(net), float(risk)))
    if not trades:
        raise ValueError("no trades with both recorded UTC entry and exit times")
    return trades


def read_m5(path: str | Path) -> list[Bar]:
    bars = []
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            bar = Bar(int(row["time"]), float(row["open"]), float(row["high"]),
                      float(row["low"]), float(row["close"]), int(row["spread_points"]))
            if (not all(math.isfinite(v) and v > 0 for v in
                        (bar.open, bar.high, bar.low, bar.close))
                    or bar.low > min(bar.open, bar.close)
                    or bar.high < max(bar.open, bar.close)
                    or bar.spread_points <= 0):
                raise ValueError("invalid M5 OHLC or missing recorded spread")
            bars.append(bar)
    bars.sort(key=lambda b: b.time)
    if any(b.time <= a.time for a, b in zip(bars, bars[1:])):
        raise ValueError("duplicate M5 bar timestamp")
    return bars


def atr_before(m15: list[Bar], when: int) -> float | None:
    """ATR from 14 complete M15 periods before the triggering M5 close."""
    done = [b for b in m15 if b.time + 900 <= when]
    if len(done) < 15:
        return None
    window = done[-15:]
    if any(b.time - a.time != 900 for a, b in zip(window, window[1:])):
        return None
    return sum(max(b.high - b.low, abs(b.high - a.close), abs(b.low - a.close))
               for a, b in zip(window, window[1:])) / 14


def test_addon(
    bars: list[Bar], trades: list[Trade], *, usd_per_price: float,
    cap_usd: float = 0.20, target_usd: float = 0.03,
    point: float = 0.01, max_spread_points: int = 50,
) -> dict:
    if not all(math.isfinite(v) and v > 0 for v in
               (usd_per_price, cap_usd, target_usd, point)) or max_spread_points < 1:
        raise ValueError("positive finite money, price point and spread required")
    # M15 boundaries require complete triples. A malformed history must fail closed.
    m15_buckets: dict[int, list[Bar]] = {}
    for bar in bars:
        m15_buckets.setdefault(bar.time - bar.time % 900, []).append(bar)
    m15 = []
    for t, parts in sorted(m15_buckets.items()):
        parts.sort(key=lambda b: b.time)
        if [b.time for b in parts] == [t, t + 300, t + 600]:
            m15.append(Bar(t, parts[0].open, max(b.high for b in parts),
                           min(b.low for b in parts), parts[-1].close,
                           max(b.spread_points for b in parts)))
    outcomes = []
    reasons: dict[str, int] = {}
    def reject(reason: str) -> None:
        reasons[reason] = reasons.get(reason, 0) + 1

    if not bars:
        raise ValueError("M5 history is empty")
    for trade in sorted(trades, key=lambda row: (row.opened_broker, row.number)):
        if trade.opened_broker < bars[0].time or trade.closed_broker > bars[-1].time + 300:
            reject("outside_history_window")
            continue
        # The reported minute may precede the fill by up to 59 seconds. Do not
        # use the possibly pre-entry part of that candle for the main anchor.
        first = next((i for i, b in enumerate(bars) if b.time > trade.opened_broker
                      and b.time + 300 < trade.closed_broker), None)
        if first is None:
            reject("no_full_m5_during_trade")
            continue
        if bars[first].time - trade.opened_broker > 300:
            reject("entry_bar_missing")
            continue
        sign = 1 if trade.direction == "BUY" else -1
        main_anchor = bars[first].open + (bars[first].spread_points * point
                                           if sign == 1 else 0)
        found = False
        for i in range(first + 2, len(bars) - 1):
            b = bars[i]
            if b.time + 300 >= trade.closed_broker:
                break
            window = bars[i - 2:i + 1]
            if any(y.time - x.time != 300 for x, y in zip(window, window[1:])):
                continue
            atr = atr_before(m15, b.time + 300)
            if atr is None or b.spread_points > max_spread_points:
                continue
            progress = sign * (b.close - main_anchor)
            aligned = sum(sign * (x.close - x.open) > 0 for x in window)
            calm = sum(x.high - x.low for x in window) / 3 <= 0.60 * atr
            if progress < 0.25 * atr or aligned < 2 or not calm:
                continue
            next_bar = bars[i + 1]
            if next_bar.time != b.time + 300 or next_bar.spread_points > max_spread_points:
                continue
            if any(other.number != trade.number
                   and other.opened_broker <= next_bar.time < other.closed_broker
                   for other in trades):
                reject("other_recorded_position_open")
                found = True
                break
            if any(existing["entry_broker"] <= next_bar.time < existing["exit_broker"]
                   for existing in outcomes):
                reject("prior_simulated_addon_open")
                found = True
                break
            stop_distance = 0.50 * atr
            risk = stop_distance * usd_per_price
            if trade.risk_units / 100 + risk > cap_usd + 1e-9:
                reject("combined_initial_risk_above_cap")
                found = True
                break
            # Require at least one entire future M5 candle while the main trade
            # is still open. This avoids using information after its actual exit.
            if next_bar.time + 300 > trade.closed_broker:
                continue
            entry = next_bar.open + (next_bar.spread_points * point if sign == 1 else 0)
            stop = entry - sign * stop_distance
            target = entry + sign * target_usd / usd_per_price
            exit_price = entry
            trigger = "TIME"
            held = 0
            for future in bars[i + 1:i + 4]:
                if future.time + 300 > trade.closed_broker or (held and
                        future.time != bars[i + held].time + 300):
                    break
                held += 1
                spread = future.spread_points * point
                stop_hit = (future.low <= stop if sign == 1 else future.high + spread >= stop)
                target_hit = (future.high >= target if sign == 1 else future.low + spread <= target)
                if stop_hit:
                    # If a candle opens beyond the stop, price the adverse gap.
                    executable_open = future.open if sign == 1 else future.open + spread
                    exit_price = min(stop, executable_open) if sign == 1 else max(stop, executable_open)
                    trigger = "STOP"
                    break
                if target_hit:
                    exit_price = target
                    trigger = "TARGET"
                    break
                exit_price = future.close if sign == 1 else future.close + spread
            if held == 0:
                continue
            pnl = sign * (exit_price - entry) * usd_per_price
            outcomes.append({"trade": trade.number, "pnl_usd": round(pnl, 6),
                             "entry_broker": next_bar.time, "exit": trigger,
                             "exit_broker": bars[i + held].time + 300,
                             "initial_risk_usd": round(risk, 6)})
            found = True
            break
        if not found:
            reject("no_eligible_completed_signal")
    return {"input_trades": len(trades), "addons": len(outcomes),
            "target_exits": sum(x["exit"] == "TARGET" for x in outcomes),
            "stop_exits": sum(x["exit"] == "STOP" for x in outcomes),
            "time_exits": sum(x["exit"] == "TIME" for x in outcomes),
            "net_usd_proxy": round(sum(x["pnl_usd"] for x in outcomes), 4),
            "rejected": reasons, "outcomes": outcomes,
            "assumptions": {"broker_usd_per_1_price_at_min_lot": usd_per_price,
                            "combined_initial_risk_cap_usd": cap_usd,
                            "addon_target_usd_before_unattributed_fees": target_usd,
                            "max_spread_points": max_spread_points,
                            "trigger_progress_atr": 0.25,
                            "addon_stop_atr": 0.50,
                            "max_hold_m5_bars": 3},
            "limitations": [
                "Main entry uses next M5 open, not the actual broker fill; report timestamps are minute resolution.",
                "M5 OHLC cannot determine intrabar order: simultaneous stop/target counts as stop.",
                "Historical spreads are recorded, but slippage, commissions and swap are unavailable.",
                "No causal comparison: opening an add-on could change margin and subsequent EA decisions.",
                "Only recorded Ramon positions are checked for overlap; other EAs and unrecorded positions are unknown.",
                "Requires hedging mode for separately ticketed positions; live EA is unchanged.",
            ]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--m5-csv", required=True)
    parser.add_argument("--report", required=True, help="Ramon performance report with UTC entry/exit lines")
    parser.add_argument("--broker-utc-offset-seconds", type=int, required=True)
    parser.add_argument("--min-lot-usd-per-price", type=float, required=True,
                        help="Exact value from InspectGoldContracts on the same account")
    parser.add_argument("--cap-usd", type=float, default=0.20)
    parser.add_argument("--target-usd", type=float, default=0.03)
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()
    trades = read_trades(args.report, broker_utc_offset=args.broker_utc_offset_seconds)
    result = test_addon(read_m5(args.m5_csv), trades,
                        usd_per_price=args.min_lot_usd_per_price,
                        cap_usd=args.cap_usd, target_usd=args.target_usd)
    if args.summary_only:
        result.pop("outcomes")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
