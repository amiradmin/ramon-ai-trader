"""Audit observed Ramon decisions and real trade outcomes around macro news.

Historical non-executed WAIT snapshots have no counterfactual P&L. This command
does not train, promote or change the live trading policy.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
from statistics import median

def _summary(rows: list[dict]) -> dict:
    executed = [row for row in rows if row["net_r"] is not None]
    learnable = [row for row in executed if row["training_status"] == "LEARNABLE"]
    return {
        "snapshots": len(rows),
        "base_buy": sum(row["decision"] == "BUY" for row in rows),
        "base_sell": sum(row["decision"] == "SELL" for row in rows),
        "base_wait": sum(row["decision"] == "WAIT" for row in rows),
        "executed_trades": len(executed),
        "learnable_trades": len(learnable),
        "net_r_executed": round(sum(row["net_r"] for row in executed), 5),
        "win_rate_executed": (round(sum(row["net_r"] > 0 for row in executed) / len(executed), 5)
                              if executed else None),
        "median_spread_atr": (round(median(row["spread_atr"] for row in rows), 5)
                              if rows else None),
    }


def audit(db: str | Path, *, symbol: str = "XAUUSD_l") -> dict:
    path = Path(db).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
        records = conn.execute("""
            SELECT s.quote_time,s.direction,s.final_decision,s.spread,s.atr,
                   s.news_features,s.model_metadata,t.net_r,t.training_status
            FROM decision_samples s
            LEFT JOIN trade_outcomes t ON t.sample_key=s.sample_key
            WHERE s.symbol=? AND s.quote_time IS NOT NULL AND s.news_features IS NOT NULL
            ORDER BY s.quote_time
        """, (symbol,)).fetchall()
    rows = []
    for quote_time, side, decision, spread, atr, raw, metadata, net_r, training_status in records:
        try:
            features = json.loads(raw)
            if "post_high_30m" not in features or not atr or float(atr) <= 0:
                continue
            context = json.loads(metadata or "{}")
            phase = context.get("news_live_context", {}).get("news_phase", "UNKNOWN")
            if phase not in {"UNAVAILABLE", "NORMAL", "PRE_RELEASE", "RELEASE_UNCONFIRMED", "POST_RELEASE"}:
                continue
            event_type = next((name for name in ("inflation", "labor", "fed", "growth")
                               if features.get(f"event_{name}", 0) >= 0.5), "other")
            rows.append({
                "quote_time": int(quote_time), "phase": phase, "event_type": event_type,
                "side": side, "decision": decision,
                "signed_surprise": float(features.get("signed_surprise", 0)),
                "spread_atr": float(spread) / float(atr),
                "net_r": float(net_r) if net_r is not None else None,
                "training_status": training_status,
            })
        except (ValueError, TypeError, KeyError, AttributeError, json.JSONDecodeError):
            continue
    phases = ("UNAVAILABLE", "NORMAL", "PRE_RELEASE", "RELEASE_UNCONFIRMED", "POST_RELEASE")
    post = [row for row in rows if row["phase"] == "POST_RELEASE"]
    return {
        "symbol": symbol,
        "all": _summary(rows),
        "phases": {phase: _summary([row for row in rows if row["phase"] == phase])
                   for phase in phases},
        "post_release_event_types": {
            name: _summary([row for row in post if row["event_type"] == name])
            for name in ("inflation", "labor", "fed", "growth", "other")
        },
        "post_release_surprise_sign": {
            name: _summary([row for row in post if (row["signed_surprise"] > 0 if name == "positive"
                     else row["signed_surprise"] < 0 if name == "negative"
                     else row["signed_surprise"] == 0)])
            for name in ("positive", "negative", "zero")
        },
        "preliminary_counts_available": (
            sum(row["net_r"] is not None for row in post) >= 20
            and sum(row["net_r"] is not None for row in rows if row["phase"] == "NORMAL") >= 20
        ),
        "scope": "Recorded snapshots and executed position outcomes only; WAIT has no "
                 "counterfactual profit. Phase groups are observational, not causal. "
                 "No intrabar fill or latency correction; never auto-enables news trades.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("/data/ramon_history.sqlite3"))
    parser.add_argument("--symbol", default="XAUUSD_l")
    args = parser.parse_args()
    print(json.dumps(audit(args.db, symbol=args.symbol), indent=2))


if __name__ == "__main__":
    main()
