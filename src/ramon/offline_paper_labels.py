"""Read-only, offline counterfactual labels from PostgreSQL decisions and M1 bars.

These are PAPER outcomes, never trade outcomes or promotion labels. Requires
known entry spread and complete post-decision M1 bars. No synthetic snapshots.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import os
from pathlib import Path


def classify_path(*, decision_time: int, direction: str, bars: list[tuple],
                  spread_price: float, target_price: float, stop_price: float,
                  max_minutes: int = 30) -> dict:
    """Conservative bar-resolution bracket replay. Entry at next M1 open.

    Bars: (UTC epoch minute start, open, high, low, close).
    Refuse to guess which barrier was hit first inside the same M1 bar.
    """
    if direction not in {"BUY", "SELL"}:
        return {"status": "SKIP_NO_DIRECTION"}
    if not bars or spread_price <= 0 or target_price <= 0 or stop_price <= 0:
        return {"status": "SKIP_MISSING_COST_OR_BARS"}
    # A new trade is entered at the open of the first *subsequent* bar,
    # not a partially observed candle or the snapshot's last price.
    start = next((i for i, b in enumerate(bars) if int(b[0]) >= (decision_time // 60 + 1) * 60), None)
    if start is None:
        return {"status": "SKIP_NO_FUTURE_BAR"}
    window = bars[start:start + max_minutes]
    if len(window) < max_minutes:
        return {"status": "SKIP_INCOMPLETE_HORIZON"}
    if any(int(b[0]) != int(window[0][0]) + i * 60 for i, b in enumerate(window)):
        return {"status": "SKIP_GAPPED_BARS"}
    if any(float(b[2]) < max(float(b[1]), float(b[4]))
           or float(b[3]) > min(float(b[1]), float(b[4])) for b in window):
        return {"status": "SKIP_INVALID_BAR"}
    entry_mid = float(window[0][1])
    sign = 1 if direction == "BUY" else -1
    # Conservatively charge a full round-trip spread, not zero costs.
    def pnl(mid):
        return sign * (mid - entry_mid) - spread_price
    for minute, bar in enumerate(window, 1):
        favorable = float(bar[2]) if sign == 1 else float(bar[3])
        adverse = float(bar[3]) if sign == 1 else float(bar[2])
        hit_tp = pnl(favorable) >= target_price
        hit_sl = pnl(adverse) <= -stop_price
        if hit_tp and hit_sl:
            return {"status": "AMBIGUOUS_BOTH_BARRIERS", "minute": minute}
        if hit_sl:
            return {"status": "SL", "minute": minute, "net_price": -stop_price}
        if hit_tp:
            return {"status": "TP", "minute": minute, "net_price": target_price}
    return {"status": "TIME_EXIT", "minute": max_minutes,
            "net_price": pnl(float(window[-1][4]))}


def run(dsn: str, symbol: str, output: Path, point: float,
        tp_points: float, sl_points: float, horizon: int, limit: int) -> dict:
    import psycopg
    if min(point, tp_points, sl_points, horizon, limit) <= 0:
        raise ValueError("point, TP, SL, horizon and limit must be positive")
    counts = {}
    written = 0
    with psycopg.connect(dsn, connect_timeout=5, options="-c statement_timeout=10000") as conn:
        with conn.cursor() as cur:
            cur.execute("BEGIN TRANSACTION READ ONLY")
            cur.execute("""
                SELECT sample_key, quote_time, direction, spread
                FROM ramon.decision_samples
                WHERE symbol=%s AND quote_time IS NOT NULL
                ORDER BY quote_time DESC LIMIT %s
            """, (symbol, limit))
            decisions = cur.fetchall()
            if not decisions:
                return {"status": "NO_SNAPSHOTS", "written": 0}
            lo = min(int(r[1]) for r in decisions)
            hi = max(int(r[1]) for r in decisions) + 60*(horizon+3)
            cur.execute("""
                SELECT time,open,high,low,close,spread_points
                FROM ramon.mtf_utc_bars
                WHERE symbol=%s AND timeframe='M1' AND time BETWEEN %s AND %s
                ORDER BY time
            """, (symbol, lo-60, hi))
            m1 = cur.fetchall()
    import bisect
    times = [int(b[0]) for b in m1]
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as out:
        writer = csv.DictWriter(out, fieldnames=["sample_key","quote_time","direction","status",
            "minute","net_price","source"])
        writer.writeheader()
        for key, when, direction, spread in decisions:
            offset = bisect.bisect_left(times, int(when))
            path = m1[offset:offset+horizon+3]
            # spread in decision_samples is a PRICE, not broker points.
            result = classify_path(decision_time=int(when), direction=str(direction),
                bars=path, spread_price=float(spread),
                target_price=tp_points*point, stop_price=sl_points*point,
                max_minutes=horizon)
            status = result["status"]
            counts[status] = counts.get(status,0)+1
            writer.writerow({"sample_key":key,"quote_time":when,"direction":direction,
                "status":status,"minute":result.get("minute",""),
                "net_price":result.get("net_price",""),"source":"PAPER_M1_COUNTERFACTUAL"})
            written+=1
    return {"status":"RESEARCH_ONLY","written":written,"by_status":counts,
            "path":str(output),"warning":"Not live fills; no slippage or tick ordering."}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbol", default="XAUUSD_l")
    p.add_argument("--output", default="/tmp/ramon_paper_m1.csv")
    p.add_argument("--point", type=float, required=True)
    p.add_argument("--tp-points", type=float, required=True)
    p.add_argument("--sl-points", type=float, required=True)
    p.add_argument("--horizon", type=int, default=30)
    p.add_argument("--limit", type=int, default=2000)
    a = p.parse_args()
    dsn=os.environ.get("RAMON_PG_DSN") or os.environ.get("RAMON_PG_TRAINING_DSN")
    if not dsn:
        p.error("RAMON_PG_DSN or RAMON_PG_TRAINING_DSN required")
    print(run(dsn,a.symbol,Path(a.output),a.point,a.tp_points,a.sl_points,a.horizon,a.limit))

if __name__=="__main__":
    main()
