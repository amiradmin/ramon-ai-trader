"""Read-only chronological audit of a fixed entry filter on actually traded positions.

This is a selection diagnostic, not a counterfactual backtest: skipping trades
would change subsequent position availability and perhaps future decisions.
"""

from __future__ import annotations

import argparse
import json
from math import isfinite
from pathlib import Path
import sqlite3

from .report import load_report_trades, safe_json, training_status


FEATURES = {
    "signal_strength": "entry_features",
    "spread_atr": "entry_features",
    "intrabar_move_atr": "entry_features",
    "uncertainty_atr": "entry_features",
}


def summarize(rows: list[dict], feature: str, threshold: float, keep: str) -> dict:
    def stats(items: list[dict]) -> dict:
        net = sum(float(row["net_units"]) for row in items)
        wins = sum(float(row["net_units"]) > 0 for row in items)
        return {"trades": len(items), "wins": wins, "net_units": round(net, 4),
                "net_r": round(sum(float(row["net_r"]) for row in items), 4)}

    matched = [row for row in rows if isinstance(row.get(feature), (float, int))]
    boundary = len(matched) * 4 // 5
    result = {"feature": feature, "threshold": threshold, "keep": keep,
              "total_trades": len(rows), "feature_coverage": len(matched),
              "cutoff": "first 80% by recorded entry order; last 20% untouched"}
    for name, period in (("earlier", matched[:boundary]), ("later", matched[boundary:])):
        selected = [row for row in period if (row[feature] >= threshold if keep == "above"
                                              else row[feature] <= threshold)]
        result[name] = {"all": stats(period), "kept": stats(selected),
                        "skipped": stats([row for row in period if row not in selected])}
    result["limitations"] = [
        "Only realized trades are observed; this does not simulate orders skipped by the filter.",
        "Do not choose a threshold using the later period or treat this as live P&L.",
        "Broker time and legacy clock offsets can differ; verify chronology before interpreting.",
        "Fewer than 20 later-period trades cannot support a reliable performance comparison."
        if result["later"]["all"]["trades"] < 20 else
        "Results remain descriptive, even with at least 20 later-period trades.",
    ]
    return result


def audit(db: str | Path, symbol: str, feature: str, threshold: float, keep: str) -> dict:
    if feature not in FEATURES or keep not in {"above", "below"} or not isfinite(threshold):
        raise ValueError("invalid fixed feature or comparison")
    uri = Path(db).expanduser().resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as con:
        con.row_factory = sqlite3.Row
        trades = load_report_trades(con, symbol)
        values = {row["sample_key"]: safe_json(row["entry_features"])
                  for row in con.execute(
                      "SELECT sample_key,entry_features FROM decision_samples WHERE symbol=?", (symbol,)
                  )}
    rows = []
    for trade in sorted(trades, key=lambda row: (row["opened"], row["trade_key"])):
        if training_status(trade) != "LEARNABLE":
            continue
        value = values.get(trade.get("sample_key"), {}).get(feature)
        if (isinstance(value, (int, float)) and not isinstance(value, bool)
                and isfinite(value)):
            trade[feature] = value
        rows.append(trade)
    return summarize(rows, feature, threshold, keep)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--feature", choices=tuple(FEATURES), required=True)
    parser.add_argument("--threshold", type=float, required=True)
    parser.add_argument("--keep", choices=("above", "below"), required=True)
    args = parser.parse_args()
    print(json.dumps(audit(args.db, args.symbol, args.feature, args.threshold, args.keep),
                     indent=2))


if __name__ == "__main__":
    main()
