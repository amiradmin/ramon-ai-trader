"""Read-only realized-trade loss diagnostics from native PostgreSQL.

Associations are descriptive, not causal. Never modify executions or labels.
"""
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def aggregate(rows):
    def group(key):
        buckets = {}
        for row in rows:
            k = str(row.get(key) or "UNKNOWN")
            b = buckets.setdefault(k, {"group": k, "trades": 0, "wins": 0,
                "gross_profit_units": 0.0, "gross_loss_units": 0.0, "net_units": 0.0})
            net = float(row["net_units"] or 0)
            b["trades"] += 1
            b["wins"] += int(net > 0)
            b["gross_profit_units"] += max(net, 0)
            b["gross_loss_units"] += max(-net, 0)
            b["net_units"] += net
        result = []
        for b in buckets.values():
            b["win_rate"] = round(b["wins"] / b["trades"], 4)
            b["profit_factor"] = round(b["gross_profit_units"] / b["gross_loss_units"], 3) if b["gross_loss_units"] else None
            for k in ("gross_profit_units", "gross_loss_units", "net_units"):
                b[k] = round(b[k], 4)
            result.append(b)
        return sorted(result, key=lambda b: (b["net_units"], -b["trades"], b["group"]))
    return {name: group(name) for name in
            ("hour_tehran", "direction", "ea_version", "exit_reason", "market_regime", "entry_source")}


def loss_report(symbol="XAUUSD_l", limit=500):
    if os.environ.get("RAMON_STORAGE_BACKEND", "").lower() != "postgres":
        return {"available": False, "reason": "POSTGRES_REQUIRED"}
    try:
        import psycopg
        from psycopg.rows import dict_row
        limit = max(1, min(2000, int(limit)))
        with psycopg.connect(os.environ["RAMON_PG_DSN"], connect_timeout=3,
                options="-c default_transaction_read_only=on -c statement_timeout=6000",
                row_factory=dict_row) as con:
            with con.cursor() as cur:
                cur.execute("""SELECT t.trade_key,t.sample_key,t.direction,t.opened,t.closed,
                          t.received,t.net_units,t.exit_reason,
                          t.telemetry->>'entry_ea_version' AS ea_version,
                          t.telemetry->>'entry_source' AS entry_source,
                          t.telemetry->>'trade_role' AS trade_role,
                          t.telemetry->>'closed_utc_offset_seconds' AS closed_offset,
                          s.metadata->'model_metadata'->'decision_audit'->'final'->>'market_state' AS regime_final,
                          s.metadata->'model_metadata'->'market_assessment'->>'state' AS regime_assessed
                       FROM ramon.trade_outcomes t
                       LEFT JOIN ramon.decision_samples s ON t.sample_key=s.sample_key
                       WHERE t.symbol=%s AND t.closed>0
                       ORDER BY t.closed DESC,t.trade_key DESC LIMIT %s""", (symbol, limit))
                records = cur.fetchall()
        rows = []
        for r in records:
            if str(r.get("trade_role") or "MAIN").upper() not in ("MAIN", "PRIMARY"):
                continue
            try:
                # Native historical timestamps may be broker-local. Prefer explicit
                # recorded UTC offset, otherwise use ingestion time for grouping.
                offset = float(r["closed_offset"]) if r["closed_offset"] is not None else None
                stamp = float(r["closed"]) - offset if offset is not None else float(r["received"])
                hour = datetime.fromtimestamp(stamp, timezone.utc).astimezone(
                    ZoneInfo("Asia/Tehran")).hour
                net = float(r["net_units"])
            except (ValueError, TypeError, OverflowError, OSError):
                continue
            rows.append({
                "trade_key": r["trade_key"],
                "hour_tehran": f"{hour:02d}:00",
                "direction": r["direction"],
                "ea_version": r["ea_version"] or "UNKNOWN",
                "exit_reason": r["exit_reason"] or "UNKNOWN",
                "market_regime": r["regime_final"] or r["regime_assessed"] or "UNKNOWN",
                "entry_source": r["entry_source"] or "UNKNOWN",
                "net_units": net,
            })
        profit = sum(max(0, r["net_units"]) for r in rows)
        loss = sum(max(0, -r["net_units"]) for r in rows)
        return {"available": True, "symbol": symbol, "sample_limit": limit,
                "trades_analyzed": len(rows), "gross_profit_units": round(profit, 4),
                "gross_loss_units": round(loss, 4), "net_units": round(profit-loss, 4),
                "profit_factor": round(profit/loss, 3) if loss else None,
                "groups": aggregate(rows),
                "caveats": [
                    "Descriptive correlations only; regime or hour does not establish cause.",
                    "Outcome rows are not independently reconciled with MT5 deals.",
                    "Time grouping uses explicit broker UTC offset when present; otherwise received time.",
                    "Unknown entry-source rows are not assumed to be automatic.",
                    "Regime is missing if not persisted in decision audit."
                ],
                "read_only": True}
    except Exception as exc:
        return {"available": False, "reason": "POSTGRES_READ_FAILED", "detail": type(exc).__name__}
