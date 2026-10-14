"""Evidence-gated prevention hypotheses for daily Ramon reviews.

Diagnostics, not execution signals. No lookahead feature selection or live toggles.
"""
from collections import defaultdict

def prevention_plan(shadow, losses, *, min_cases=8):
    cases=shadow.get("cases") or []
    clusters=defaultdict(lambda: {"tp":0,"sl":0,"ambiguous":0,"unknown":0})
    for row in cases:
        reason=str(row.get("reason") or "UNKNOWN")
        status=row.get("status")
        bucket=clusters[reason]
        if status=="SIMULATED_TP": bucket["tp"]+=1
        elif status=="SIMULATED_SL": bucket["sl"]+=1
        elif status=="AMBIGUOUS_SAME_BAR": bucket["ambiguous"]+=1
        else: bucket["unknown"]+=1
    proposals=[]
    for reason,stats in sorted(clusters.items(),key=lambda pair:-(pair[1]["tp"]+pair[1]["sl"])):
        resolved=stats["tp"]+stats["sl"]
        if resolved<min_cases:continue
        proposals.append({
            "id":"WAIT_REASON_REVIEW","condition":reason,"observations":stats,
            "hypothesis":"Audit whether this WAIT rule rejects candidates with favorable simulated outcomes.",
            "status":"NEEDS_HOLDOUT_AND_BROKER_VALIDATION",
            "live_enabled":False,
            "required_tests":["independent days and forward shadow run",
                              "same costs and entry/exit rules for control and candidate",
                              "MT5 gate, spread, quote freshness, and execution feasibility"]})
    by_exit=defaultdict(lambda:{"count":0,"net_units":0.0})
    for trade in losses:
        key=str(trade.get("exit_reason") or "UNKNOWN")
        by_exit[key]["count"]+=1
        by_exit[key]["net_units"]+=float(trade.get("net_units") or 0)
    for key,stats in sorted(by_exit.items(),key=lambda pair:pair[1]["net_units"]):
        if stats["count"]<2:continue
        proposals.append({
            "id":"LOSS_EXIT_REVIEW","condition":key,
            "observations":{"count":stats["count"],"net_units":round(stats["net_units"],4)},
            "hypothesis":"Inspect entry conditions, initial risk, stop distance and exit execution for repeated losses.",
            "status":"NEEDS_MT5_DEAL_AUDIT",
            "live_enabled":False,
            "required_tests":["broker deal reconciliation and fill-level costs",
                              "risk-normalized comparisons by version and entry source",
                              "independent out-of-sample shadow evaluation"]})
    if not proposals:
        proposals.append({"id":"INSUFFICIENT_EVIDENCE","condition":"sample",
                          "hypothesis":"Continue logging independent cases before suggesting a rule change.",
                          "status":"COLLECT_DATA","live_enabled":False,
                          "required_tests":["more independent completed M1 paths and verified trade outcomes"]})
    return {"mode":"SHADOW_ONLY","live_changes":0,
            "evaluation_protocol":{"selection":"historical diagnostic",
              "validation":"separate future days with no tuning on holdout",
              "promotion":"explicit human review after statistically meaningful cost-aware performance",
              "current_status":"NOT_VALIDATED"},
            "proposals":proposals[:15]}
