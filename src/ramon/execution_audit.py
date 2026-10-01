from __future__ import annotations

import argparse
import math
import sqlite3
import statistics
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TimingRow:
    opened: int
    captured: int
    quote_time: int | None
    signal_bar_time: int
    opened_utc_offset_seconds: int | None


def _pct(n: int, d: int) -> float:
    return 100.0 * n / d if d else math.nan


def _fmt(value: float, digits: int = 2) -> str:
    if math.isnan(value):
        return "N/A"
    return f"{value:.{digits}f}"


def load_joined(con: sqlite3.Connection, symbol: str) -> list[sqlite3.Row]:
    return list(
        con.execute(
            """SELECT
                   t.trade_key,t.direction,t.opened,t.closed,
                   t.opened_utc_offset_seconds,t.actual_fill_price,
                   s.captured,s.quote_time,s.signal_bar_time,
                   s.mid,s.spread,s.stop_distance,s.target_distance
               FROM trade_outcomes t
               JOIN decision_samples s
                 ON s.sample_key=t.sample_key
                AND s.symbol=t.symbol
               WHERE t.symbol=?
               ORDER BY t.opened,t.trade_key""",
            (symbol,),
        )
    )


def estimate_point(con: sqlite3.Connection, symbol: str) -> tuple[float | None, int]:
    """Estimate symbol point from matched decision spread / stored spread_points."""
    rows = list(
        con.execute(
            """SELECT s.spread,h.spread_points
               FROM decision_samples s
               JOIN history_bars h
                 ON h.symbol=s.symbol
                AND h.timeframe='M15'
                AND h.time=s.signal_bar_time
               WHERE s.symbol=?
                 AND s.spread>0
                 AND h.spread_points>0""",
            (symbol,),
        )
    )
    estimates = [
        float(row["spread"]) / int(row["spread_points"])
        for row in rows
        if int(row["spread_points"]) > 0
    ]
    if not estimates:
        return None, 0
    return statistics.median(estimates), len(estimates)


def timing_audit(rows: list[sqlite3.Row]) -> dict[str, object]:
    timing = [
        TimingRow(
            opened=int(row["opened"]),
            captured=int(row["captured"]),
            quote_time=int(row["quote_time"]) if row["quote_time"] is not None else None,
            signal_bar_time=int(row["signal_bar_time"]),
            opened_utc_offset_seconds=(
                int(row["opened_utc_offset_seconds"])
                if row["opened_utc_offset_seconds"] is not None
                else None
            ),
        )
        for row in rows
    ]
    canonical = [row for row in timing if row.opened_utc_offset_seconds is not None]
    quote_coverage = sum(row.quote_time is not None for row in canonical)

    def utc(value: int, row: TimingRow) -> int:
        assert row.opened_utc_offset_seconds is not None
        return value - row.opened_utc_offset_seconds

    opened_after_capture = sum(utc(row.opened, row) >= row.captured for row in canonical)
    opened_after_quote = sum(
        row.quote_time is not None and utc(row.opened, row) >= utc(row.quote_time, row)
        for row in canonical
    )
    signal_before_open = sum(
        utc(row.signal_bar_time, row) < utc(row.opened, row) for row in canonical
    )

    capture_to_open = [utc(row.opened, row) - row.captured for row in canonical]
    quote_to_open = [
        utc(row.opened, row) - utc(row.quote_time, row)
        for row in canonical
        if row.quote_time is not None
    ]
    signal_age = [
        utc(row.opened, row) - utc(row.signal_bar_time, row) for row in canonical
    ]

    return {
        "trades": len(timing),
        "canonical_coverage": len(canonical),
        "quote_coverage": quote_coverage,
        "opened_after_capture": opened_after_capture,
        "opened_after_quote": opened_after_quote,
        "signal_before_open": signal_before_open,
        "capture_to_open": capture_to_open,
        "quote_to_open": quote_to_open,
        "signal_age": signal_age,
    }


def _stats(values: list[int]) -> tuple[float, float, float] | None:
    if not values:
        return None
    ordered = sorted(values)
    return (
        statistics.median(ordered),
        ordered[max(0, int(0.05 * (len(ordered) - 1)))],
        ordered[min(len(ordered) - 1, int(0.95 * (len(ordered) - 1)))],
    )


def bar_spread_coverage(con: sqlite3.Connection, symbol: str) -> tuple[int, int, float]:
    total, nonzero = con.execute(
        """SELECT COUNT(*), SUM(CASE WHEN spread_points>0 THEN 1 ELSE 0 END)
           FROM history_bars WHERE symbol=? AND timeframe='M15'""",
        (symbol,),
    ).fetchone()
    total = int(total or 0)
    nonzero = int(nonzero or 0)
    return nonzero, total, _pct(nonzero, total)


def sell_exit_side_sensitivity(
    con: sqlite3.Connection,
    rows: list[sqlite3.Row],
    symbol: str,
    *,
    point: float | None,
    max_bars: int,
) -> dict[str, int | float]:
    """Compare SELL trigger outcomes on raw OHLC vs spread-adjusted Ask proxy.

    The raw stored OHLC is treated as the chart/bar side. The adjusted path adds
    each bar's stored spread_points * point to OHLC as an Ask proxy. Bars lacking
    spread telemetry are excluded from the adjusted comparison.
    """
    result = {
        "eligible_sell": 0,
        "compared": 0,
        "changed_exit": 0,
        "raw_tp_adjusted_sl": 0,
        "raw_time_adjusted_exit": 0,
    }
    if point is None or point <= 0:
        return result

    bars = list(
        con.execute(
            """SELECT time,high,low,close,spread_points
               FROM history_bars
               WHERE symbol=? AND timeframe='M15'
               ORDER BY time""",
            (symbol,),
        )
    )
    for row in rows:
        if str(row["direction"]) != "SELL":
            continue
        result["eligible_sell"] += 1
        stop_distance = float(row["stop_distance"] or 0)
        target_distance = float(row["target_distance"] or 0)
        spread = float(row["spread"] or 0)
        mid = float(row["mid"] or 0)
        if stop_distance <= 0 or target_distance <= 0 or spread <= 0:
            continue

        entry = mid - spread / 2.0
        stop = entry + stop_distance
        target = entry - target_distance
        next_bar = (int(row["opened"]) // 900 + 1) * 900
        future = [b for b in bars if int(b["time"]) >= next_bar][:max_bars]
        if not future or any(int(b["spread_points"] or 0) <= 0 for b in future):
            continue

        raw_exit = "TIME"
        adjusted_exit = "TIME"
        for b in future:
            high = float(b["high"])
            low = float(b["low"])
            if high >= stop:
                raw_exit = "SL"
                break
            if low <= target:
                raw_exit = "TP"
                break

        for b in future:
            offset = int(b["spread_points"]) * point
            ask_high = float(b["high"]) + offset
            ask_low = float(b["low"]) + offset
            if ask_high >= stop:
                adjusted_exit = "SL"
                break
            if ask_low <= target:
                adjusted_exit = "TP"
                break

        result["compared"] += 1
        if raw_exit != adjusted_exit:
            result["changed_exit"] += 1
            if raw_exit == "TP" and adjusted_exit == "SL":
                result["raw_tp_adjusted_sl"] += 1
            if raw_exit == "TIME" and adjusted_exit != "TIME":
                result["raw_time_adjusted_exit"] += 1

    return result


def print_report(db: str | Path, symbol: str, *, max_bars: int) -> None:
    path = Path(db).expanduser()
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        rows = load_joined(con, symbol)
        timing = timing_audit(rows)
        point, point_samples = estimate_point(con, symbol)
        nonzero, total_bars, coverage = bar_spread_coverage(con, symbol)
        sensitivity = sell_exit_side_sensitivity(
            con, rows, symbol, point=point, max_bars=max_bars
        )

        print("=== RAMON EXECUTION AUDIT ===")
        print(f"Symbol                  : {symbol}")
        print(f"Matched executed trades : {len(rows)}")
        print()
        print("=== 1) ENTRY TIMING / ALIGNMENT ===")
        print(
            f"UTC canonical coverage   : {timing['canonical_coverage']}/{timing['trades']} "
            f"({_fmt(_pct(int(timing['canonical_coverage']), int(timing['trades'])))}%)"
        )
        print(
            f"quote_time UTC coverage  : {timing['quote_coverage']}/{timing['canonical_coverage']}"
        )
        print(
            f"opened_utc >= captured   : {timing['opened_after_capture']}/{timing['canonical_coverage']}"
        )
        print(
            f"opened_utc >= quote_utc  : {timing['opened_after_quote']}/{timing['quote_coverage']}"
        )
        print(
            f"signal_utc < opened_utc  : {timing['signal_before_open']}/{timing['canonical_coverage']}"
        )
        for label, key in (
            ("capture -> open sec", "capture_to_open"),
            ("quote -> open sec", "quote_to_open"),
            ("signal bar age sec", "signal_age"),
        ):
            stats = _stats(timing[key])
            if stats is None:
                print(f"{label:24s}: N/A")
            else:
                median, p05, p95 = stats
                print(
                    f"{label:24s}: median={median:.0f} p05={p05:.0f} p95={p95:.0f}"
                )
        fill_rows = [row for row in rows if row["actual_fill_price"] is not None]
        print(f"Actual fill price        : {len(fill_rows)}/{len(rows)} stored")
        if fill_rows:
            slips = []
            for row in fill_rows:
                mid = float(row["mid"])
                spread = float(row["spread"])
                expected = mid + spread / 2.0 if row["direction"] == "BUY" else mid - spread / 2.0
                fill = float(row["actual_fill_price"])
                adverse = fill - expected if row["direction"] == "BUY" else expected - fill
                slips.append(adverse)
            print(
                f"entry adverse slippage   : median={statistics.median(slips):.5f} "
                f"mean={statistics.mean(slips):.5f} price units"
            )
        print("Timing basis             : broker times canonicalized with opened_utc_offset_seconds")
        print()

        print("=== 2) BID / ASK BAR SEMANTICS ===")
        print("EA source path           : M15 OHLC comes from MT5 CopyRates; live Bid/Ask sent separately")
        print("Stored Ask OHLC          : NO")
        print(
            f"bar spread coverage      : {nonzero}/{total_bars} ({_fmt(coverage)}%)"
        )
        if point is None:
            print("estimated point           : N/A (insufficient matched spread telemetry)")
        else:
            print(
                f"estimated point           : {point:.10g} from {point_samples} matched samples"
            )
        print(
            "BUY exit-side note       : raw Bid-like OHLC is directionally compatible with BUY exits"
        )
        print(
            "SELL exit-side note      : SELL exits require Ask; raw OHLC alone can bias SL/TP ordering"
        )
        print()

        print("=== 3) SELL ASK-PROXY SENSITIVITY ===")
        print(f"SELL trades eligible     : {sensitivity['eligible_sell']}")
        print(f"SELL paths compared      : {sensitivity['compared']}")
        print(f"exit classification changed: {sensitivity['changed_exit']}")
        print(f"raw TP -> Ask-proxy SL   : {sensitivity['raw_tp_adjusted_sl']}")
        print(f"raw TIME -> Ask exit     : {sensitivity['raw_time_adjusted_exit']}")
        if int(sensitivity["compared"]) > 0:
            changed_pct = _pct(
                int(sensitivity["changed_exit"]), int(sensitivity["compared"])
            )
            print(f"changed fraction         : {_fmt(changed_pct)}%")
        else:
            print("changed fraction         : N/A")
        print()
        print("=== AUDIT CONCLUSION ===")
        print("1. Existing Validation Lab is valid as a matched-timing research benchmark,")
        print("   but SELL path results should not be treated as broker-exact until Ask-side")
        print("   history or tick data is available.")
        print("2. Entry timestamps are auditable from persisted data, but actual fill-price")
        print("   slippage is not because fill price is not stored in trade_outcomes.")
        print("3. Recommended telemetry upgrade: persist actual entry fill price and either")
        print("   Ask OHLC/ticks or enough tick history to resolve SELL exits exactly.")
    finally:
        con.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit Ramon entry timing and Bid/Ask replay semantics")
    parser.add_argument("--db", default="/data/ramon_history.sqlite3")
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--max-bars", type=int, default=24)
    args = parser.parse_args()
    if args.max_bars < 1:
        parser.error("--max-bars must be >= 1")
    print_report(args.db, args.symbol, max_bars=args.max_bars)


if __name__ == "__main__":
    main()
