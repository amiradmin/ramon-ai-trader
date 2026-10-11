"""Read-only, cost-inclusive outcome audit of Ramon trades.

Groups by UTC entry hour, direction, entry version and observed entry conditions.
Descriptive statistics only; no policy optimization or trade instructions.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections import defaultdict, Counter
from datetime import datetime, timezone
from pathlib import Path


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _bucket(value, thresholds):
    if value is None:
        return "MISSING"
    for bound in thresholds:
        if value < bound:
            return f"<{bound}"
    return f">={thresholds[-1]}"


def _group_stats(rows):
    valid = [r for r in rows if r["_net_r"] is not None]
    wins = sum(r["_net_r"] > 0 for r in valid)
    losses = sum(r["_net_r"] < 0 for r in valid)
    flat = len(valid) - wins - losses
    profits = sum(max(0.0, r["_net_r"]) for r in valid)
    losses_r = -sum(min(0.0, r["_net_r"]) for r in valid)
    net_units = [r["_net_units"] for r in valid if r["_net_units"] is not None]
    return {
        "trades": len(rows), "valid_net_r": len(valid),
        "wins": wins, "losses": losses, "flat": flat,
        "win_rate": round(wins / len(valid), 6) if valid else None,
        "net_r_sum": round(sum(r["_net_r"] for r in valid), 6),
        "mean_net_r": round(sum(r["_net_r"] for r in valid) / len(valid), 6) if valid else None,
        "profit_factor_r": round(profits / losses_r, 6) if losses_r > 0 else None,
        "profit_factor_r_reason": "NO_LOSSES_OR_NO_VALID_R" if losses_r == 0 else None,
        "net_account_units_sum": round(sum(net_units), 6),
        "net_units_available": len(net_units),
    }


def summarize(rows):
    indexed = []
    missing = Counter()
    for original in rows:
        r = dict(original)
        opened = r.get("opened")
        try:
            hour = datetime.fromtimestamp(int(opened), timezone.utc).hour
        except (ValueError, TypeError, OverflowError, OSError):
            hour = None
            missing["entry_timestamp"] += 1
        r["_hour"] = f"{hour:02d}" if hour is not None else "MISSING"
        r["_net_r"] = _finite(r.get("net_r"))
        r["_net_units"] = _finite(r.get("net_units"))
        r["_atr"] = _finite(r.get("atr"))
        r["_spread"] = _finite(r.get("spread"))
        r["_stop_distance"] = _finite(r.get("stop_distance"))
        r["_target_distance"] = _finite(r.get("target_distance"))
        if r["_net_r"] is None:
            missing["net_r"] += 1
        if r.get("matched_sample") is None:
            missing["matched_decision"] += 1
        if r["_atr"] is None:
            missing["atr"] += 1
        if r["_spread"] is None:
            missing["spread"] += 1
        r["_regime"] = "UNCLASSIFIED"
        # Do not extract a supposed regime from loosely structured JSON.
        r["_manual"] = "YES" if r.get("manual_intervention") == 1 or r.get("exit_detail") == "manual_dashboard_close" else "UNKNOWN_OR_NO"
        indexed.append(r)
    groups = {
        "entry_hour_utc": lambda r: r["_hour"],
        "direction": lambda r: r.get("direction") or "MISSING",
        "exit_reason": lambda r: r.get("exit_reason") or "MISSING",
        "exit_detail": lambda r: r.get("exit_detail") or "MISSING",
        "entry_version": lambda r: r.get("entry_ea_version") or "MISSING",
        "trade_role": lambda r: r.get("trade_role") or "MISSING",
        "manual_close": lambda r: r["_manual"],
        "spread_price_bucket": lambda r: _bucket(r["_spread"], (0.3, 0.5, 0.8)),
        "atr_price_bucket": lambda r: _bucket(r["_atr"], (3.0, 8.0, 15.0)),
    }
    grouped = {}
    for name, key_fn in groups.items():
        buckets = defaultdict(list)
        for r in indexed:
            buckets[str(key_fn(r))].append(r)
        grouped[name] = {key: _group_stats(buckets[key]) for key in sorted(buckets)}
    return {
        "mode": "READ_ONLY_TRADE_OUTCOME_AUDIT",
        "total_trades": len(indexed),
        "overall": _group_stats(indexed),
        "missing_fields": dict(sorted(missing.items())),
        "groups": grouped,
        "limitations": [
            "net_r uses stored realized, cost-inclusive results; profit_factor_r is calculated in R units, not account units.",
            "Hourly groups use trade opened timestamps interpreted as UTC; verify their provenance before time-of-day decisions.",
            "Regime labels are not inferred from unvalidated nested decision JSON.",
            "ATR and spread buckets use stored decision samples, not necessarily exact fill-time values.",
            "Multiple rows can refer to the same original decision; grouping is descriptive, not independent-sample validation.",
            "Manual-close UNKNOWN_OR_NO is not proof of no intervention; NULL indicates missing telemetry.",
            "No causal inference, future-data leakage control, or profitability guarantee from these summaries.",
        ],
    }


def read_db(db: Path, symbol: str) -> list[dict]:
    with sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(x) for x in conn.execute("""
            SELECT t.trade_key,t.sample_key,t.symbol,t.direction,t.opened,t.closed,
                   t.net_r,t.net_units,t.exit_reason,t.exit_detail,
                   t.entry_ea_version,t.trade_role,t.manual_intervention,
                   d.sample_key AS matched_sample,d.atr,d.spread,
                   d.stop_distance,d.target_distance
            FROM trade_outcomes t
            LEFT JOIN decision_samples d ON t.sample_key=d.sample_key AND t.symbol=d.symbol
            WHERE t.symbol=?
            ORDER BY t.opened,t.trade_key
        """, (symbol,))]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = summarize(read_db(args.db, args.symbol))
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
