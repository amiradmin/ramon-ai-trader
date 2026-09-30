"""Read-only evidence for MAIN/SMALL, fixed entry screens and dollar readiness.

No threshold search, model promotion, account switching or live order submission.
Unknown units/timestamps/provenance remain unknown; never assume CENT conversion.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import random
import sqlite3
from statistics import mean

from .report import load_report_trades, safe_json, training_status


def role(row: dict) -> str:
    declared = row.get("trade_role")
    magic = {26092212: "MAIN", 26092213: "SMALL"}.get(row.get("entry_magic"))
    if declared in {"MAIN", "SMALL"} and (magic is None or magic == declared):
        return declared
    return "UNKNOWN"


def metrics(rows: list[dict]) -> dict:
    values = [float(r["net_units"]) for r in rows]
    gains = sum(max(v, 0) for v in values)
    losses = -sum(min(v, 0) for v in values)
    converted = [r for r in rows if r.get("money_units_per_usd") is not None
                 and math.isfinite(float(r["money_units_per_usd"]))
                 and float(r["money_units_per_usd"]) > 0]
    equity = peak = dd = 0.0
    for r in sorted(converted, key=lambda r: (r["closed"] - (r.get("closed_utc_offset_seconds") or 0), r["trade_key"])):
        equity += float(r["net_units"]) / float(r["money_units_per_usd"])
        peak = max(peak, equity)
        dd = max(dd, peak - equity)
    wins = [v for v in values if v > 0]
    losing = [-v for v in values if v < 0]
    conversions = {float(r["money_units_per_usd"]) for r in converted}
    homogeneous = len(conversions) <= 1 and len(converted) == len(rows)
    return {"trades": len(rows), "wins": len(wins), "losses": len(losing),
            "win_rate": len(wins) / len(rows) if rows else None,
            "net_units": sum(values) if homogeneous else None,
            "unit_totals_comparable": homogeneous,
            "profit_factor": gains / losses if losses and homogeneous else None,
            "expectancy_units": mean(values) if values and homogeneous else None,
            "average_win_units": mean(wins) if wins and homogeneous else None,
            "average_loss_units": mean(losing) if losing and homogeneous else None,
            "break_even_win_rate": mean(losing) / (mean(wins) + mean(losing))
            if wins and losing and homogeneous else None,
            "net_usd_covered": sum(float(r["net_units"]) / float(r["money_units_per_usd"])
                                   for r in converted),
            "usd_coverage": len(converted), "closed_drawdown_usd_covered": dd}


def profile_identity(row: dict) -> str | None:
    metadata = safe_json(row.get("model_metadata"))
    execution = metadata.get("execution_profile")
    audit = metadata.get("decision_audit", {})
    if not isinstance(execution, dict) or not execution or not audit.get("settings"):
        return None
    identity = {"execution": execution, "settings": audit["settings"],
                "model": row.get("chronos_model"), "revision": metadata.get("chronos_revision"),
                "bundle": row.get("bundle_id"), "active": metadata.get("ensemble_active"),
                "code": metadata.get("strategy_code_fingerprint")}
    if not identity["model"] or not identity["revision"] or not identity["code"]:
        return None
    return hashlib.sha256(json.dumps(identity, sort_keys=True, allow_nan=False).encode()).hexdigest()


def readiness(rows: list[dict], *, since_utc: int, version: str, capital_usd: float,
              expected_roles: tuple[str, ...] = ("MAIN", "SMALL")) -> dict:
    if since_utc <= 0 or not version or not math.isfinite(capital_usd) or capital_usd <= 0:
        raise ValueError("explicit UTC start, version and positive capital required")
    cohort = []
    uncertain_clock = 0
    for r in rows:
        if str(r.get("entry_ea_version")) != version:
            continue
        if r.get("opened_utc_offset_seconds") is None or r.get("closed_utc_offset_seconds") is None:
            uncertain_clock += 1
            continue
        if int(r["opened"]) - int(r["opened_utc_offset_seconds"]) >= since_utc:
            cohort.append(r)
    cohort.sort(key=lambda r: (r["closed"] - r["closed_utc_offset_seconds"], r["trade_key"]))
    summary = metrics(cohort)
    daily = defaultdict(float)
    for r in cohort:
        if r.get("money_units_per_usd") and float(r["money_units_per_usd"]) > 0:
            day = datetime.fromtimestamp(r["closed"] - r["closed_utc_offset_seconds"], timezone.utc).date()
            daily[str(day)] += r["net_units"] / r["money_units_per_usd"]
    # Resample entire active UTC days, preserving same-day dependence between both EAs.
    rng = random.Random(60)
    days = list(daily.values())
    samples = sorted(mean(rng.choices(days, k=len(days))) for _ in range(2000)) if days else []
    lower = samples[49] if samples else None
    per_role = {}
    for name in expected_roles:
        group = [r for r in cohort if role(r) == name]
        ids = [profile_identity(r) for r in group]
        per_role[name] = {**metrics(group), "profiles": len(set(ids) - {None}),
                          "missing_profiles": ids.count(None)}
    half = len(cohort) // 2
    halves = [metrics(cohort[:half]), metrics(cohort[half:])]
    fees_complete = all(all(r.get(k) is not None for k in
                        ("profit_units", "commission_units", "swap_units", "fee_units")) for r in cohort)
    fees_reconciled = fees_complete and all(abs(sum(float(r[k]) for k in
                        ("profit_units", "commission_units", "swap_units", "fee_units"))
                        - float(r["net_units"])) < 1e-5 for r in cohort)
    risk_complete = all(r.get("initial_risk_units", 0) > 0 and r.get("max_executable_risk_usd", 0)
                        and r.get("money_units_per_usd", 0) for r in cohort)
    caps_ok = risk_complete and all(r["initial_risk_units"] <=
                  r["max_executable_risk_usd"] * r["money_units_per_usd"] + 1e-5 for r in cohort)
    checks = {
        "at_least_100_new_trades": len(cohort) >= 100,
        "at_least_20_active_utc_days": len(days) >= 20,
        "timestamps_complete": uncertain_clock == 0,
        "usd_conversion_complete": summary["usd_coverage"] == len(cohort) and bool(cohort),
        "fees_reconciled": fees_reconciled and bool(cohort),
        "unambiguous_exit_labels": bool(cohort) and all(training_status(r) == "LEARNABLE" for r in cohort),
        "positive_net": summary["net_usd_covered"] > 0,
        "profit_factor_at_least_1_2": (summary["profit_factor"] or 0) >= 1.2,
        "both_chronological_halves_positive": all(h["net_usd_covered"] > 0 for h in halves),
        "positive_daily_bootstrap_lower_bound": lower is not None and lower > 0,
        "closed_drawdown_at_most_10_percent": summary["closed_drawdown_usd_covered"] <= capital_usd * .10,
        "recorded_per_trade_caps_respected": bool(cohort) and caps_ok,
        "expected_roles_only": all(role(r) in expected_roles for r in cohort),
        "each_role_positive_and_fixed": all(g["trades"] >= 20 and g["net_usd_covered"] > 0
                         and g["profiles"] == 1 and g["missing_profiles"] == 0 for g in per_role.values()),
    }
    return {"status": "EVIDENCE_PASSED_CONTRACT_REVIEW_REQUIRED" if all(checks.values()) else "NOT_READY",
            "checks": checks, "summary": summary, "roles": per_role, "active_utc_days": len(days),
            "daily_mean_usd_lower_95_percent": lower, "since_utc": since_utc, "version": version,
            "limitations": ["Research gates chosen before new observations; no income guarantee.",
                            "Closed-trade drawdown excludes open losses and intra-trade equity troughs.",
                            "Bootstrap days can remain serially dependent; the interval is approximate.",
                            "Must verify dollar-account contract, minimum lot, margin, spread and stop costs separately."]}


def entry_audit(rows: list[dict]) -> dict:
    """Fixed hypotheses applied separately to old/new chronological closed trades.

    This is descriptive selection of executed trades, not a portfolio backtest:
    skipping a trade could allow a different later entry not present in this data.
    """
    ordered = sorted(rows, key=lambda r: (r["opened"], r["trade_key"]))
    split = len(ordered) * 7 // 10
    result = {}
    for name, group in (("earlier_70_percent", ordered[:split]), ("later_30_percent", ordered[split:])):
        screens = defaultdict(list)
        unknown = 0
        for r in group:
            meta = safe_json(r.get("model_metadata"))
            base = meta.get("decision_audit", {}).get("base", {})
            direction = r["direction"]
            if not base:
                unknown += 1
                continue
            screens["baseline_known"].append(r)
            if (base.get("intrabar_confirmed") and base.get("intrabar_direction") == direction
                and base.get("ai_trend_confirmed") and base.get("ai_trend_direction") == direction):
                screens["both_direction_confirmations"].append(r)
            regime = safe_json(r.get("regime_features"))
            sign = 1 if direction == "BUY" else -1
            if all(regime.get(k) is not None and sign * float(regime[k]) > 0
                   for k in ("ret_1_atr", "ret_4_atr", "ret_12_atr")):
                screens["aligned_15_60_180_minute_returns"].append(r)
            if r.get("min_lot_override_used") == 0:
                screens["no_minimum_lot_override"].append(r)
            news = meta.get("news_live_context", {})
            if news.get("news_source_ready") and not safe_json(r.get("news_features")).get("high_impact_near", 1):
                screens["confirmed_news_clear"].append(r)
        result[name] = {"unknown_entry_audit": unknown,
                        "screens": {k: metrics(v) for k, v in screens.items()}}
    return {"periods": result, "live_changes": False,
            "limitations": "Executed-trade subsets only, no causal improvement claim; do not select a winner on this sample."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--since-utc", required=True, help="Predeclared forward-test start, ISO 8601 with timezone")
    parser.add_argument("--version", default="0.60")
    parser.add_argument("--capital-usd", type=float, default=30)
    parser.add_argument("--roles", nargs="+", choices=("MAIN", "SMALL"), default=["MAIN", "SMALL"])
    args = parser.parse_args()
    start = datetime.fromisoformat(args.since_utc.replace("Z", "+00:00"))
    if start.tzinfo is None:
        parser.error("--since-utc must include a timezone")
    with sqlite3.connect(Path(args.db).resolve().as_uri() + "?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        con.execute("BEGIN")
        rows = load_report_trades(con, args.symbol)
    print(json.dumps({"roles": {name: metrics([r for r in rows if role(r) == name])
                                for name in ("MAIN", "SMALL", "UNKNOWN")},
                      "entry_audit": entry_audit(rows),
                      "readiness": readiness(rows, since_utc=int(start.timestamp()), version=args.version,
                                            capital_usd=args.capital_usd, expected_roles=tuple(args.roles))},
                     indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
