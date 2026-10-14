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


def dashboard_opportunity_audit(rows):
    """Descriptive breakdown without treating unknown source as automatic."""
    selected = [r for r in rows if r.get("entry_source") == "DASHBOARD_OPPORTUNITY"]
    def total(items):
        gains = sum(max(0.0, float(r["net_units"])) for r in items)
        losses = sum(max(0.0, -float(r["net_units"])) for r in items)
        return {"trades": len(items), "gross_profit_units": round(gains, 4),
                "gross_loss_units": round(losses, 4), "net_units": round(gains-losses, 4),
                "profit_factor": round(gains/losses, 3) if losses else None}
    worst = sorted(selected, key=lambda r: float(r["net_units"]))[:25]
    fields = ("trade_key", "ea_version", "direction", "exit_reason", "exit_detail",
              "net_units", "net_r", "initial_risk_units", "risk_budget_units",
              "min_lot_override_used", "risk_cap_usd", "hour_tehran")
    return {
        "overall": total(selected),
        "by_exit_reason": [
            dict({"exit_reason": reason}, **total([r for r in selected if r["exit_reason"] == reason]))
            for reason in sorted({r["exit_reason"] for r in selected})
        ],
        "by_ea_version": [
            dict({"ea_version": version}, **total([r for r in selected if r["ea_version"] == version]))
            for version in sorted({r["ea_version"] for r in selected})
        ],
        "worst_trades": [{field: row.get(field) for field in fields} for row in worst],
        "missing_initial_risk": sum(r.get("initial_risk_units") is None for r in selected),
        "unknown_ea_version": sum(r.get("ea_version") == "UNKNOWN" for r in selected),
        "scope": "DASHBOARD_OPPORTUNITY only; independent MT5 deal reconciliation pending"
    }


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
                          t.received,t.net_units,t.exit_reason,t.initial_risk_units,t.net_r,
                          t.telemetry->>'entry_ea_version' AS ea_version,
                          t.telemetry->>'entry_source' AS entry_source,
                          t.telemetry->>'trade_role' AS trade_role,
                          t.telemetry->>'closed_utc_offset_seconds' AS closed_offset,
                          t.telemetry->>'exit_detail' AS exit_detail,
                          t.telemetry->>'min_lot_override_used' AS min_lot_override,
                          t.telemetry->>'risk_budget_units' AS risk_budget_units,
                          t.telemetry->>'max_executable_risk_usd' AS risk_cap_usd,
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
                "initial_risk_units": r["initial_risk_units"],
                "net_r": r["net_r"],
                "exit_detail": r["exit_detail"],
                "risk_budget_units": r["risk_budget_units"],
                "min_lot_override_used": r["min_lot_override"],
                "risk_cap_usd": r["risk_cap_usd"],
            })
        profit = sum(max(0, r["net_units"]) for r in rows)
        loss = sum(max(0, -r["net_units"]) for r in rows)
        missing = {field: sum(row[field] == "UNKNOWN" for row in rows)
                   for field in ("ea_version", "market_regime", "entry_source")}
        stop_rows = [row for row in rows if row["exit_reason"] in
                     ("DEAL_REASON_SL", "DEAL_REASON_SO")]
        worst_stops = sorted(stop_rows, key=lambda row: row["net_units"])[:20]
        def stop_view(row):
            return {field: row.get(field) for field in (
                "trade_key", "exit_reason", "exit_detail", "net_units", "net_r",
                "initial_risk_units", "risk_budget_units", "min_lot_override_used",
                "risk_cap_usd", "direction", "hour_tehran", "ea_version", "market_regime")}
        stop_summary = {
            "count": len(stop_rows),
            "net_units": round(sum(row["net_units"] for row in stop_rows), 4),
            "worst_trades": [stop_view(row) for row in worst_stops],
        }
        return {"available": True, "symbol": symbol, "sample_limit": limit,
                "missing_metadata": missing, "stop_loss_audit": stop_summary,
                "dashboard_opportunity_audit": dashboard_opportunity_audit(rows),
                "trades_analyzed": len(rows), "gross_profit_units": round(profit, 4),
                "gross_loss_units": round(loss, 4), "net_units": round(profit-loss, 4),
                "profit_factor": round(profit/loss, 3) if loss else None,
                "groups": aggregate(rows),
                "caveats": [
                    "Descriptive correlations only; regime or hour does not establish cause.",
                    "Outcome rows are not independently reconciled with MT5 deals.",
                    "Time grouping uses explicit broker UTC offset when present; otherwise received time.",
                    "Unknown entry-source rows are not assumed to be automatic.",
                    "Regime is missing if not persisted in decision audit.",
                    "Legacy SQLite fields may be absent from PostgreSQL telemetry. UNKNOWN values must not be guessed.",
                    "Stop-loss initial risk has not been verified against original MT5 deals."
                ],
                "read_only": True}
    except Exception as exc:
        return {"available": False, "reason": "POSTGRES_READ_FAILED", "detail": type(exc).__name__}
