"""PostgreSQL-native, read-only Ramon closed-trade report.

Uses the operational trade ledger without SQLite or legacy training snapshots.
Financial amounts remain in account units; no inferred currency conversion.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone


def summarize_trades(rows):
    wins = [r for r in rows if r["net_units"] > 0]
    losses = [r for r in rows if r["net_units"] < 0]
    gain = sum(r["net_units"] for r in wins)
    loss = -sum(r["net_units"] for r in losses)
    return {
        "trades": len(rows),
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(rows)-len(wins)-len(losses),
        "net_units": sum(r["net_units"] for r in rows),
        "net_r": sum(r["net_r"] for r in rows),
        "profit_factor": gain/loss if loss else None,
        "by_direction": dict(Counter(r["direction"] for r in rows)),
        "by_exit_reason": dict(Counter(r["exit_reason"] for r in rows)),
    }


def load_postgres_trades(dsn: str, symbol: str):
    import psycopg
    from psycopg.rows import dict_row
    if not dsn:
        raise ValueError("RAMON_PG_DSN is required")
    with psycopg.connect(dsn, connect_timeout=5,
                         options="-c statement_timeout=30000",
                         row_factory=dict_row) as db:
        with db.cursor() as cur:
            cur.execute("""
                SELECT trade_key, sample_key, symbol, direction, opened, closed,
                       net_units, net_r, exit_reason, telemetry
                FROM ramon.trade_outcomes
                WHERE symbol=%s ORDER BY closed,trade_key
            """, (symbol,))
            return cur.fetchall()


def generate_postgres_report(dsn: str, symbol: str, limit: int):
    rows = load_postgres_trades(dsn, symbol)
    s = summarize_trades(rows)
    print("RAMON POSTGRESQL CLOSED-TRADE REPORT")
    print(f"Symbol: {symbol}  Closed trades: {s['trades']}")
    print(f"Wins / Losses / BE: {s['wins']} / {s['losses']} / {s['breakeven']}")
    print(f"Net account units: {s['net_units']:+.4f}  Net R: {s['net_r']:+.4f}")
    print("Profit factor:", "inf" if s["profit_factor"] is None else f"{s['profit_factor']:.3f}")
    print("By direction:", s["by_direction"])
    print("By exit reason:", s["by_exit_reason"])
    shown = rows if limit <= 0 else rows[-limit:]
    for r in shown:
        closed = datetime.fromtimestamp(r["closed"], timezone.utc).isoformat()
        print(f"{closed} {r['trade_key']} {r['direction']} {r['net_units']:+.4f} {r['net_r']:+.4f}R {r['exit_reason']}")
    print("Note: detailed legacy excursion/feature analysis is not yet included.")
