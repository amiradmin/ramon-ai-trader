"""Read-only trace from saved model decisions to real closed MT5 positions."""
from __future__ import annotations

import argparse
import json
from math import isfinite
from pathlib import Path
import sqlite3

from .report import safe_json, trade_time


def _number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _future_close(con: sqlite3.Connection, symbol: str, bar_time: int, horizon: int) -> float | None:
    """Use only consecutive, recorded completed M15 bars at the forecast horizon."""
    if not 1 <= horizon <= 24:
        return None
    rows = con.execute(
        """SELECT time,close FROM history_bars
           WHERE symbol=? AND timeframe='M15' AND time>? AND time<=? ORDER BY time""",
        (symbol, bar_time, bar_time + horizon * 900),
    ).fetchall()
    if len(rows) != horizon or any(row["time"] != bar_time + (i + 1) * 900
                                   for i, row in enumerate(rows)):
        return None
    return _number(rows[-1]["close"])


def build_report(db: str | Path, symbol: str = "XAUUSD_l", limit: int = 20) -> dict:
    """Read a consistent SQLite snapshot without schema migration or inference calls."""
    if limit < 0:
        raise ValueError("limit must be >=0 (0 means all closed trades)")
    path = Path(db).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        con.execute("BEGIN")
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"decision_samples", "trade_outcomes"} <= tables:
            raise ValueError("decision_samples and trade_outcomes tables are required")
        cols = {r[1] for r in con.execute("PRAGMA table_info(decision_samples)")}
        outcome_cols = {r[1] for r in con.execute("PRAGMA table_info(trade_outcomes)")}
        names = ("sample_key", "signal_bar_time", "quote_time", "base_decision", "final_decision",
                 "direction", "mid", "spread", "model_metadata", "chronos_model", "bundle_id")
        select = ",".join(f"s.{name} AS sample_{name}" if name in cols else
                          f"NULL AS sample_{name}" for name in names)
        join = "t.sample_key=s.sample_key AND t.symbol=s.symbol" if "sample_key" in cols else "0"
        rows = [dict(row) for row in con.execute(
            f"SELECT t.*, {select} FROM trade_outcomes t "
            f"LEFT JOIN decision_samples s ON {join} "
            "WHERE t.symbol=? ORDER BY t.closed DESC,t.trade_key DESC", (symbol,))]
        counts = {"BUY": 0, "SELL": 0, "WAIT": 0, "UNKNOWN": 0}
        if "final_decision" in cols:
            for value, count in con.execute(
                "SELECT final_decision,COUNT(*) FROM decision_samples WHERE symbol=? GROUP BY final_decision",
                (symbol,),
            ):
                counts[value if value in counts else "UNKNOWN"] += count
        else:
            counts["UNKNOWN"] = con.execute(
                "SELECT COUNT(*) FROM decision_samples WHERE symbol=?", (symbol,)
            ).fetchone()[0]
        has_bars = "history_bars" in tables
        details = []
        for row in rows:
            metadata = safe_json(row.get("sample_model_metadata"))
            audit = metadata.get("decision_audit") or {}
            if not isinstance(audit, dict):
                audit = {}
            base = audit.get("base") if isinstance(audit.get("base"), dict) else {}
            final = audit.get("final") if isinstance(audit.get("final"), dict) else {}
            settings = audit.get("settings") if isinstance(audit.get("settings"), dict) else {}
            forecast = _number(base.get("forecast_median"))
            horizon = settings.get("horizon")
            bar_time = row.get("sample_signal_bar_time")
            realized = None
            if (has_bars and forecast is not None and forecast > 0 and isinstance(horizon, int)
                    and isinstance(bar_time, int)):
                realized = _future_close(con, symbol, bar_time, horizon)
            quote = row.get("sample_quote_time")
            delay = int(row["opened"]) - int(quote) if quote is not None else None
            fees = ("commission_units", "swap_units", "fee_units")
            cost_known = all(name in outcome_cols and row[name] is not None for name in fees)
            parts = ("profit_units", *fees)
            breakdown_known = (cost_known and all(name in outcome_cols and row[name] is not None
                                                   for name in parts))
            spread = _number(row.get("sample_spread"))
            mid = _number(row.get("sample_mid"))
            final_decision = final.get("decision") or row.get("sample_final_decision")
            if row.get("sample_sample_key") is None:
                join_status = "NO_SAVED_DECISION"
            elif final_decision not in {"BUY", "SELL"}:
                join_status = "FINAL_DECISION_UNKNOWN_OR_WAIT"
            elif final_decision != row["direction"]:
                join_status = "DIRECTION_MISMATCH"
            else:
                join_status = "MATCHED_DIRECTION"
            item = {
                "trade_key": row["trade_key"], "sample_key": row["sample_key"],
                "decision_joined": row.get("sample_sample_key") is not None,
                "decision_to_trade_status": join_status,
                "direction": row["direction"], "base_decision": base.get("decision") or row.get("sample_base_decision"),
                "base_reason": base.get("reason"),
                "final_decision": final_decision,
                "final_reason": final.get("reason"),
                "model": row.get("sample_chronos_model"), "bundle": row.get("sample_bundle_id"),
                "forecast_median": forecast, "forecast_horizon_bars": horizon if isinstance(horizon, int) else None,
                "realized_m15_bid_close": realized,
                "forecast_error_bid": (round(forecast - realized, 5) if realized is not None else None),
                "quote_bid": (mid - spread / 2 if mid is not None and spread is not None else None),
                "quote_ask": (mid + spread / 2 if mid is not None and spread is not None else None),
                "quoted_spread": spread,
                "quote_to_open_seconds": delay if delay is not None and 0 <= delay <= 90 else None,
                "quote_to_open_status": ("unknown" if delay is None else
                                         "within_90s" if 0 <= delay <= 90 else "outside_90s"),
                "opened": trade_time(row, "opened") if "opened" in row else None,
                "closed": trade_time(row, "closed") if "closed" in row else None,
                "profit_units": row.get("profit_units") if breakdown_known else None,
                "broker_fees_units": sum(float(row[name]) for name in fees) if cost_known else None,
                "net_units": row["net_units"], "net_r": row["net_r"],
                "exit_reason": row["exit_reason"],
                "execution_fill_price": None,
                "slippage_price": None,
            }
            details.append(item)
        shown = details if limit == 0 else details[:limit]
        return {
            "symbol": symbol,
            "coverage": {
                "saved_decisions": sum(counts.values()), "decisions_by_final_side": counts,
                "closed_trades": len(details),
                "joined_by_sample_key": sum(x["decision_joined"] for x in details),
                "forecast_error_available": sum(x["forecast_error_bid"] is not None for x in details),
                "complete_broker_fees": sum(x["broker_fees_units"] is not None for x in details),
                "quote_to_open_within_90s": sum(x["quote_to_open_seconds"] is not None for x in details),
                "direction_mismatches": sum(x["final_decision"] in {"BUY", "SELL"} and
                                            x["final_decision"] != x["direction"] for x in details),
                "joined_without_matching_final_decision": sum(
                    x["decision_joined"] and x["decision_to_trade_status"] != "MATCHED_DIRECTION"
                    for x in details),
            },
            "summary": {"net_units": round(sum(float(x["net_units"]) for x in details), 4),
                        "net_r": round(sum(float(x["net_r"]) for x in details), 4)},
            "trades": shown,
            "notes": [
                "Forecast error compares the stored horizon forecast with the recorded future M15 Bid close; this is not trade P/L.",
                "Quoted bid/ask and spread are at decision time, not broker execution fills.",
                "Actual fill prices and slippage are not persisted in this database; they remain unknown.",
                "Broker fees are shown only when all commission, swap and fee components were recorded; net already includes them.",
                "An unmatched decision does not prove that the EA attempted and rejected an order.",
            ],
        }


def _fmt(value: object, digits: int = 4) -> str:
    return f"{value:+.{digits}f}" if isinstance(value, (float, int)) else "UNKNOWN"


def print_report(report: dict) -> None:
    coverage = report["coverage"]
    print(f"=== RAMON DECISION -> EXECUTION -> RESULT ({report['symbol']}) ===")
    print(f"Saved decisions: {coverage['saved_decisions']} {coverage['decisions_by_final_side']}")
    print(f"Closed trades: {coverage['closed_trades']} | exact decision joins: {coverage['joined_by_sample_key']}")
    print(f"Forecasts with completed horizon bars: {coverage['forecast_error_available']}"
          f" | complete broker fees: {coverage['complete_broker_fees']}"
          f" | quote-to-open <=90s: {coverage['quote_to_open_within_90s']}")
    print(f"Direction mismatches: {coverage['direction_mismatches']}"
          f" | joined without matching final decision: {coverage['joined_without_matching_final_decision']}"
          f" | total net={_fmt(report['summary']['net_units'])} account units"
          f" | total={_fmt(report['summary']['net_r'])}R")
    print("Times with recorded offsets are UTC; other times are explicitly broker-server time.")
    for item in report["trades"]:
        print(f"\n{item['trade_key']} | {item['direction']} | {item['opened']} -> {item['closed']}")
        print(f"  Join={item['decision_to_trade_status']}"
              f" | Chronos={item['model'] or 'UNKNOWN'} | base={item['base_decision'] or 'UNKNOWN'}"
              f" ({item['base_reason'] or 'UNKNOWN'}) | final={item['final_decision'] or 'UNKNOWN'}"
              f" ({item['final_reason'] or 'UNKNOWN'})")
        print(f"  Forecast median={_fmt(item['forecast_median'])}"
              f" | observed horizon Bid close={_fmt(item['realized_m15_bid_close'])}"
              f" | forecast error={_fmt(item['forecast_error_bid'])}")
        print(f"  Quote Bid/Ask={_fmt(item['quote_bid'])}/{_fmt(item['quote_ask'])}"
              f" | spread={_fmt(item['quoted_spread'])}"
              f" | quote->open={str(item['quote_to_open_seconds']) + 's' if item['quote_to_open_seconds'] is not None else 'UNKNOWN'}"
              f" ({item['quote_to_open_status']})")
        print(f"  Gross={_fmt(item['profit_units'])} | broker fees={_fmt(item['broker_fees_units'])}"
              f" | net={_fmt(item['net_units'])} units / {_fmt(item['net_r'])}R"
              f" | exit={item['exit_reason']}")
    print("\nNotes:")
    for note in report["notes"]:
        print("- " + note)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--limit", type=int, default=20, help="Most recent closed trades; 0 means all")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = build_report(args.db, args.symbol, args.limit)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print_report(report)


if __name__ == "__main__":
    main()
