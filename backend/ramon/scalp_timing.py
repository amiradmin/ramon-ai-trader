"""Read-only, deterministic M1/M5 directional context, not order authorization."""
from __future__ import annotations
import sqlite3
import time


def scalp_timing(con: sqlite3.Connection, symbol: str, now: int | None = None) -> dict:
    now = int(time.time() if now is None else now)
    result = {"status": "INSUFFICIENT_DATA", "direction": "WAIT", "m1_direction": "WAIT",
              "m5_direction": "WAIT", "reason": "M1/M5 closed bars unavailable"}
    bars = {}
    for tf, period, max_age in (("M1", 60, 180), ("M5", 300, 900)):
        data = con.execute("""SELECT time,open,high,low,close FROM mtf_utc_bars
                           WHERE symbol=? AND timeframe=? AND time<=?
                           ORDER BY time DESC LIMIT 4""", (symbol, tf, now-period)).fetchall()
        if len(data) < 4:
            result["reason"] = f"{tf}: fewer than 4 closed bars"
            return result
        data.reverse()
        if any(data[i+1][0]-data[i][0] != period for i in range(3)):
            result["reason"] = f"{tf}: candle gap"
            return result
        age = now-int(data[-1][0])
        if not (period <= age <= max_age):
            result["reason"] = f"{tf}: stale or future bars ({age}s)"
            return result
        bars[tf] = data
    # Simple observable price momentum, not a fitted probability.
    for tf, data in bars.items():
        change = float(data[-1][4])-float(data[0][4])
        direction = "BUY" if change>0 else "SELL" if change<0 else "WAIT"
        result[tf.lower()+"_direction"] = direction
        result[tf.lower()+"_change"] = change
    same = result["m1_direction"] == result["m5_direction"]
    result["direction"] = result["m1_direction"] if same else "WAIT"
    result["status"] = "AGREE" if same and result["direction"]!="WAIT" else "CONFLICT"
    result["reason"] = "Closed M1/M5 momentum agrees" if result["status"]=="AGREE" else "M1/M5 momentum conflict"
    return result
