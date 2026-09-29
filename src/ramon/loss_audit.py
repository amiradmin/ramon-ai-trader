"""Read-only loss decomposition and fixed risk-cap shadow on an MT5 report.

This is an observational selection audit. A skipped position could change later
signals, so retained historical P/L is never described as a backtest return.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import re


ROW = re.compile(
    r"^(?P<number>\d+) \| (?:WIN|LOSS|BE)\s+\| (?P<direction>BUY|SELL)\s+\| "
    r"(?P<opened>.*?) -> (?P<closed>.*?) \| net=\s*(?P<net>[+-]?\d+(?:\.\d+)?)"
    r" \| risk=\s*(?P<risk>\d+(?:\.\d+)?) \| R=\s*[+-]?\d+(?:\.\d+)"
    r" \| (?P<reason>.*?) \| model=.*?\bEA=(?P<version>\S+)",
    re.MULTILINE,
)
UTC_TIME = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d) UTC$")


def parse_report(report: str) -> list[dict]:
    trades = []
    for m in ROW.finditer(report):
        data = m.groupdict()
        opened = UTC_TIME.match(data["opened"])
        closed = UTC_TIME.match(data["closed"])
        duration = None
        if opened and closed:
            duration = int((datetime.strptime(closed[1], "%Y-%m-%d %H:%M")
                            - datetime.strptime(opened[1], "%Y-%m-%d %H:%M"))
                           .total_seconds() / 60)
            if duration < 0:
                raise ValueError("negative trade duration")
        trades.append({"number": int(data["number"]),
                       "direction": data["direction"], "net_units": float(data["net"]),
                       "risk_units": float(data["risk"]), "reason": data["reason"],
                       "version": data["version"], "duration_minutes": duration})
    if not trades or len({t["number"] for t in trades}) != len(trades):
        raise ValueError("report has no trades or has duplicate trade numbers")
    return sorted(trades, key=lambda row: row["number"])


def _stats(rows: list[dict]) -> dict:
    return {"count": len(rows), "net_units": round(sum(t["net_units"] for t in rows), 2),
            "wins": sum(t["net_units"] > 0 for t in rows),
            "stop_losses": sum("/ stop_loss" in t["reason"] for t in rows)}


def audit(trades: list[dict], *, cap_usd: float = 0.10,
          money_units_per_usd: float = 100, holdout_count: int = 20) -> dict:
    if cap_usd <= 0 or money_units_per_usd <= 0 or holdout_count < 1:
        raise ValueError("positive cap, account unit conversion and holdout required")
    # 0.29+ includes immutable initial sizing fields. The report's `risk` is
    # actual filled risk, a proxy for the risk calculable before entry.
    exact = [t for t in trades if re.fullmatch(r"0\.(?:29|[3-9]\d|\d{3,})", t["version"])]
    if len(exact) <= holdout_count:
        raise ValueError("insufficient recent trades for a held-out period")
    cap_units = cap_usd * money_units_per_usd
    def partition(rows: list[dict]) -> dict:
        kept = [t for t in rows if t["risk_units"] <= cap_units + 1e-9]
        skipped = [t for t in rows if t["risk_units"] > cap_units + 1e-9]
        return {"all": _stats(rows), "within_cap": _stats(kept),
                "above_cap": _stats(skipped)}

    stop = [t for t in trades if "/ stop_loss" in t["reason"]]
    managed = [t for t in trades if "DEAL_REASON_EXPERT" in t["reason"]]
    durations = sorted(t["duration_minutes"] for t in stop if t["duration_minutes"] is not None)
    return {
        "report_trades": _stats(trades),
        "exit_decomposition": {"stop_loss": _stats(stop), "managed": _stats(managed),
                               "stop_loss_duration_utc_covered": len(durations),
                               "stop_loss_median_minutes": durations[len(durations)//2]
                               if durations else None},
        "fixed_cap_shadow": {
            "cap_usd": cap_usd, "money_units_per_usd": money_units_per_usd,
            "exact_sizing_proxy_trades": len(exact),
            "earlier": partition(exact[:-holdout_count]),
            "later_untouched": partition(exact[-holdout_count:]),
        },
        "limitations": [
            "The cap is compared with filled risk, not a saved pre-order estimate; fills may differ.",
            "Retained historical P/L is not a counterfactual: skipped orders change later availability and decisions.",
            "Choosing the cap after looking at the later period invalidates it as an independent holdout.",
            "UTC minute timestamps give approximate durations; legacy broker-time rows are excluded.",
            "No price path or intrabar order is reconstructed, so earlier exits cannot be claimed beneficial.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True)
    parser.add_argument("--cap-usd", type=float, default=0.10)
    parser.add_argument("--money-units-per-usd", type=float, default=100)
    parser.add_argument("--holdout-count", type=int, default=20)
    args = parser.parse_args()
    result = audit(parse_report(Path(args.report).read_text(encoding="utf-8")),
                   cap_usd=args.cap_usd, money_units_per_usd=args.money_units_per_usd,
                   holdout_count=args.holdout_count)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
