"""Sampled live P/L paths for paired early-exit research; never issues orders."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sqlite3

from .report import load_report_trades


SCHEMA = """CREATE TABLE IF NOT EXISTS position_observations (
trade_key TEXT NOT NULL, quote_time INTEGER NOT NULL, captured INTEGER NOT NULL,
bid REAL NOT NULL, ask REAL NOT NULL, profit_units REAL NOT NULL, volume REAL NOT NULL,
PRIMARY KEY(trade_key,quote_time))"""


def persist_observation(db: str, payload: dict, captured: int) -> bool:
    row = payload.get("position_observation")
    if row is None:
        return False
    if not isinstance(row, dict) or not isinstance(row.get("trade_key"), str) or not row["trade_key"]:
        raise ValueError("invalid position observation")
    at = int(payload["quote_time"])
    profit, bid, ask = float(row["profit_units"]), float(payload["bid"]), float(payload["ask"])
    volume = float(row["volume"])
    if at <= 0 or not all(math.isfinite(v) for v in (profit, bid, ask, volume)) or volume <= 0 or not 0 < bid < ask:
        raise ValueError("invalid observation prices")
    with sqlite3.connect(db) as con:
        con.execute(SCHEMA)
        con.execute("INSERT OR IGNORE INTO position_observations VALUES (?,?,?,?,?,?,?)",
                    (row["trade_key"], at, captured, bid, ask, profit, volume))
    return True


def simulate_path(trade: dict, observations: list[dict], *, fee_buffer_r: float = .05) -> dict:
    """Apply fixed candidates in time order to observed marks only.

    Missing boundary coverage / gaps above 90 seconds invalidate a comparison.
    Observations stop at the real close; policies that never trigger use actual net.
    P/L marks include spread and swap but exclude commissions; sampled marks are
    not executable fills. The additional buffer is an explicit estimate, not a fee.
    """
    if not math.isfinite(fee_buffer_r) or fee_buffer_r < 0:
        raise ValueError("invalid fill/cost buffer")
    risk = float(trade["initial_risk_units"])
    rows = sorted((r for r in observations if trade["opened"] <= r["quote_time"] < trade["closed"]),
                  key=lambda r: r["quote_time"])
    if (risk <= 0 or not trade.get("planned_volume")
        or any(abs(r["volume"] - trade["planned_volume"]) > 1e-8 for r in rows)
        or len(rows) < 3 or rows[0]["quote_time"] - trade["opened"] > 60
        or trade["closed"] - rows[-1]["quote_time"] > 60
        or any(b["quote_time"] - a["quote_time"] > 90 for a, b in zip(rows, rows[1:]))
        or any(trade.get(k) is None for k in ("commission_units", "fee_units"))):
        return {"comparable": False, "reason": "insufficient sampled path or recorded fees"}
    fees = float(trade["commission_units"]) + float(trade["fee_units"])
    actual = float(trade["net_units"]) / risk
    values = {"actual": actual, "lock_half_r_giveback_quarter_r": actual, "adverse_half_r_after_120s": actual}
    triggered = set()
    peak = 0.0
    for row in rows:
        net_r = (row["profit_units"] + fees) / risk - fee_buffer_r
        peak = max(peak, net_r)
        if ("lock" not in triggered and peak >= .50 and net_r <= peak - .25):
            values["lock_half_r_giveback_quarter_r"] = net_r
            triggered.add("lock")
        if ("adverse" not in triggered and row["quote_time"] - trade["opened"] >= 120 and net_r <= -.50):
            values["adverse_half_r_after_120s"] = net_r
            triggered.add("adverse")
    return {"comparable": True, "outcomes_r": values, "observed_peak_r": peak,
            "sample_count": len(rows), "buffer_r": fee_buffer_r}


def audit(db: str, symbol: str, buffer_r: float) -> dict:
    with sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        con.execute("BEGIN")
        trades = load_report_trades(con, symbol)
        exists = con.execute("SELECT 1 FROM sqlite_master WHERE name='position_observations'").fetchone()
        paths = {}
        if exists:
            for row in con.execute("SELECT * FROM position_observations ORDER BY quote_time"):
                paths.setdefault(row["trade_key"], []).append(dict(row))
    paired = [simulate_path(t, paths.get(t["trade_key"], []), fee_buffer_r=buffer_r) for t in trades]
    valid = [p for p in paired if p["comparable"]]
    totals = {name: sum(p["outcomes_r"][name] for p in valid)
              for name in ("actual", "lock_half_r_giveback_quarter_r", "adverse_half_r_after_120s")}
    return {"closed_trades": len(trades), "paired_coverage": len(valid), "paired_net_r": totals,
            "live_changes": False, "estimated_extra_buffer_r": buffer_r,
            "limitations": "Sampled marks at snapshot cadence, not tick-exact fills. Fees estimated from realized trade; no post-close path, partial-exit modeling, gap execution or policy selection."}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", required=True)
    p.add_argument("--symbol", default="XAUUSD_l")
    p.add_argument("--buffer-r", type=float, default=.05)
    a = p.parse_args()
    print(json.dumps(audit(a.db, a.symbol, a.buffer_r), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
