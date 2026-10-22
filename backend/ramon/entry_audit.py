"""Read-only role, entry-risk and post-SL diagnostics for closed trades."""
from __future__ import annotations

import math


def trade_role(row: dict) -> str:
    explicit = row.get("trade_role")
    inferred = {26092212: "MAIN", 26092213: "SMALL"}.get(row.get("entry_magic"))
    if explicit in {"MAIN", "SMALL"}:
        return explicit if inferred is None or inferred == explicit else "UNKNOWN"
    return inferred or "UNKNOWN"


def utc_event(row: dict, field: str) -> int | None:
    offset = row.get(field + "_utc_offset_seconds")
    return None if offset is None else int(row[field]) - int(offset)


def reentry_candidates(trades: list[dict]) -> list[dict]:
    """Find observed same-role/account entries within 30 minutes after a full SL.

    Known UTC events only. This is an evidence ledger, not a replay of the
    terminal's authoritative history: missing/manual/live outcomes can break it.
    """
    groups: dict[tuple, list[dict]] = {}
    for row in trades:
        role = trade_role(row)
        if role == "UNKNOWN" or utc_event(row, "opened") is None or utc_event(row, "closed") is None:
            continue
        # EA trade keys include broker/account identity before the position ID.
        key = str(row["trade_key"])
        if ":" not in key:
            continue
        groups.setdefault((key.rsplit(":", 1)[0], role), []).append(row)
    result = []
    for (_, role), rows in groups.items():
        for current in rows:
            opened = utc_event(current, "opened")
            prior = sorted(
                (r for r in rows if r is not current and utc_event(r, "closed") <= opened),
                key=lambda r: (utc_event(r, "closed"), str(r["trade_key"])), reverse=True,
            )
            streak = []
            for row in prior:
                if (row.get("exit_reason") != "DEAL_REASON_SL" or float(row["net_units"]) >= 0
                        or row.get("direction") != current.get("direction")):
                    break
                streak.append(row)
            if not streak:
                continue
            gap = opened - utc_event(streak[0], "closed")
            if 0 <= gap < 1800:
                result.append({"trade": current, "role": role, "previous": streak[0],
                               "gap_seconds": gap, "observed_sl_streak": len(streak)})
    return sorted(result, key=lambda r: utc_event(r["trade"], "opened"))


def print_entry_audit(trades: list[dict]) -> None:
    print("=== MAIN / SMALL ENTRY RISK AUDIT ===")
    for role in ("MAIN", "SMALL", "UNKNOWN"):
        rows = [r for r in trades if trade_role(r) == role]
        if not rows:
            continue
        gains = sum(max(float(r["net_units"]), 0) for r in rows)
        losses = -sum(min(float(r["net_units"]), 0) for r in rows)
        pf = gains / losses if losses else math.inf
        print(f"{role} | trades={len(rows)} | net={gains-losses:+.4f} units | PF={pf:.3f}")
        known = [r for r in rows if all(r.get(k) is not None for k in (
            "risk_budget_units", "initial_risk_units", "max_executable_risk_usd", "money_units_per_usd"))]
        over_budget = sum(float(r["initial_risk_units"]) > float(r["risk_budget_units"])+1e-5 for r in known)
        over_cap = sum(float(r["initial_risk_units"]) > float(r["max_executable_risk_usd"])*float(r["money_units_per_usd"])+1e-5 for r in known)
        print(f"  exact risk coverage={len(known)}/{len(rows)} | above preferred budget={over_budget} | above entry-time cap={over_cap}")
        print(f"  recorded minimum-lot overrides={sum(r.get('min_lot_override_used') == 1 for r in rows)}")
    print("Roles use recorded role/magic only; no inference from profit size or EA version.")
    print("Filled risk may exceed a pre-order cap after fill changes; this does not prove a sizing bug.")
    print()
    print("=== SAME-DIRECTION REENTRY AFTER SL (UTC EVIDENCE) ===")
    known = sum(trade_role(r) != "UNKNOWN" and utc_event(r, "opened") is not None
                and utc_event(r, "closed") is not None for r in trades)
    candidates = reentry_candidates(trades)
    print(f"Role/time coverage={known}/{len(trades)} | reentries within 30min={len(candidates)}")
    print(f"After >=2 observed consecutive SLs: {sum(r['observed_sl_streak'] >= 2 for r in candidates)} review candidates")
    for item in candidates[-10:]:
        row = item["trade"]
        print(f"{item['role']} {row['direction']} {row['trade_key']} | after={item['previous']['trade_key']} | gap={item['gap_seconds']}s | observedSLs={item['observed_sl_streak']} | net={float(row['net_units']):+.4f} units")
    print("One SL does not arm the current two-SL cooldown. Rows do not prove a historical policy violation.")
    print("Closed, uploaded, known-UTC trades only; missing outcomes, partial exits and terminal history can change the streak.")
    print("No counterfactual P/L, execution changes or training labels are created by this audit.")
    print()
