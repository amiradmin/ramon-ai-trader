"""Explain the fixed-cost TREND_PULLBACK experiment from lab JSON.

This is an offline diagnostic, not a parameter optimizer or live-trading tool.
"""
import argparse
import json
from collections import Counter
from pathlib import Path


def rejection_counts(strategy):
    """The research runner records a bounded top-25 list per scenario."""
    counts=Counter()
    for item in strategy.get("rejections") or []:
        stage=str(item.get("stage") or "UNKNOWN")
        reason=str(item.get("reason") or "UNKNOWN")
        counts[(stage,reason)]+=int(item.get("count") or 0)
    return counts


def analyze(report):
    if report.get("mode")!="ISOLATED_READ_ONLY_LABORATORY":
        raise ValueError("Invalid laboratory report")
    scenarios=report.get("cost_sensitivity") or []
    if not scenarios:
        raise ValueError("No cost sensitivity runs in this report")
    baseline=next((x for x in scenarios if x.get("name")=="BASE_COST"),None)
    if baseline is None:
        raise ValueError("No BASE_COST scenario")
    base=baseline.get("trend_pullback") or {}
    base_reasons=rejection_counts(base)
    rows=[]
    for scenario in scenarios:
        trend=scenario.get("trend_pullback") or {}
        reasons=rejection_counts(trend)
        all_keys=set(base_reasons)|set(reasons)
        changes=sorted((
            {"stage":key[0],"reason":key[1],
             "baseline_count":base_reasons[key],
             "scenario_count":reasons[key],
             "delta":reasons[key]-base_reasons[key]} for key in all_keys
        ),key=lambda x:(-abs(x["delta"]),x["stage"],x["reason"]))
        rows.append({
            "scenario":scenario.get("name"),
            "spread_points":scenario.get("spread_points"),
            "slippage_points":scenario.get("slippage_points"),
            "signals":trend.get("signals"),
            "closed_trades":trend.get("closed_trades"),
            "net_gain":trend.get("net_gain"),
            "evaluations":trend.get("evaluations"),
            "rejection_total":trend.get("rejection_total"),
            "top_rejection_changes":changes[:15]})
    return {
       "mode":"OFFLINE_RESEARCH_COST_ATTRIBUTION",
       "status":"DIAGNOSTIC_ONLY_NOT_OPTIMIZED",
       "source":report.get("market_source"),
       "symbol":report.get("symbol"),
       "comparisons":rows,
       "cautions":[
         "Rejection counts are pipeline outcomes, not causal proof that a single filter explains a changed trade count.",
         "Only top 25 rejection reasons are stored per scenario; omitted reasons are not represented in deltas.",
         "Different spread/slippage may change entries, equity and later opportunity eligibility.",
         "Zero executed trades is not evidence of zero transaction cost.",
         "Current broker snapshot does not verify historical spreads, fills or cent denomination.",
         "No research parameter or live trading threshold was changed."
       ],
       "live_eligible":False
    }


def main():
    parser=argparse.ArgumentParser(description="Offline analysis of TREND_PULLBACK cost rejections")
    parser.add_argument("report")
    args=parser.parse_args()
    payload=json.loads(Path(args.report).read_text(encoding="utf-8"))
    print(json.dumps(analyze(payload),indent=2))


if __name__=="__main__":
    main()
