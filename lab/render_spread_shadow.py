"""Render the isolated higher-spread study as a self-contained offline HTML report."""
import argparse
from html import escape
import json
from pathlib import Path


def e(x):
    return escape(str(x if x is not None else "—"), quote=True)


def fmt(x):
    return "—" if x is None else (f"{x:,.2f}" if isinstance(x,(int,float)) else e(x))


def table(headers, rows):
    return ("<div class='wrap'><table><thead><tr>"+
            "".join("<th>"+e(x)+"</th>" for x in headers)+
            "</tr></thead><tbody>"+
            "".join("<tr>"+"".join("<td>"+e(value)+"</td>" for value in row)+
                    "</tr>" for row in rows)+
            "</tbody></table></div>")


def render(payload):
    if payload.get("mode")!="SPREAD_SHADOW_READ_ONLY_RESEARCH":
        raise ValueError("Expected SPREAD_SHADOW_READ_ONLY_RESEARCH report")
    scenarios=payload.get("scenarios") or []
    cohort=payload.get("matched_spread_control_comparisons") or []
    if len(scenarios)!=5 or len(cohort)!=2:
        raise ValueError("Incomplete five-scenario spread shadow experiment")
    summary=[]
    period_rows=[]
    filter_rows=[]
    for s in scenarios:
        m=s.get("metrics") or {}
        gap=s.get("gap_unaffected") or {}
        summary.append((s["name"],fmt(s["spread_points"]),fmt(s["absolute_spread_limit_points"]),
                        m.get("closed_trades"),fmt(m.get("net_gain")),fmt(m.get("profit_factor")),
                        fmt(m.get("max_drawdown_pct")),fmt(gap.get("net_gain")),
                        gap.get("closed_trades")))
        for period in s.get("periods") or []:
            period_rows.append((s["name"],period.get("name"),period.get("closed_trades"),
                                fmt(period.get("net_gain")),fmt(period.get("profit_factor")),
                                fmt(period.get("max_closed_trade_drawdown_pct")),
                                period.get("gap_affected_trades")))
        for reason in m.get("rejections") or []:
            filter_rows.append((s["name"],reason.get("stage"),reason.get("reason"),
                                reason.get("count")))
    additional=[]
    for comp in cohort:
        p=comp["cohorts"]
        new=p["newly_admitted"]
        unaffected=p["newly_admitted_gap_unaffected"]
        displaced=p["displaced_control_trades"]
        additional.append((comp["modeled_spread_points"],comp["strict_limit"],comp["expanded_limit"],
                           new["closed_trades"],fmt(new["net_gain"]),
                           fmt(new["profit_factor"]),fmt(new["max_closed_trade_drawdown_pct"]),
                           unaffected["closed_trades"],fmt(unaffected["net_gain"]),
                           displaced["closed_trades"]))
    notes="".join("<li>"+e(x)+"</li>" for x in payload.get("limitations") or [])
    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ramon — high-spread shadow research</title>
<style>
:root{color-scheme:dark}body{background:#090f1c;color:#eaf1ff;font:15px system-ui,sans-serif;
margin:0 auto;padding:28px;max-width:1200px}h1{font-size:30px;margin-bottom:5px}
h2{margin-top:35px;font-size:19px}p,li{line-height:1.6;color:#b9c8dd}
small,.muted{color:#8ea4bd}.notice{padding:18px;border-radius:10px;
border:1px solid #ad7e32;background:#302315}.wrap{overflow-x:auto}
table{border-collapse:collapse;width:100%;background:#111c2d;border-radius:8px;overflow:hidden}
th,td{text-align:left;padding:12px;border-bottom:1px solid #2d3c50;white-space:nowrap}
th{background:#17263b;color:#f5c46e}tr:hover td{background:#18283c}
</style></head><body>
<small>RAMON / ISOLATED MARKET RESEARCH LAB — NO LIVE TRADING</small>
<h1>Higher-spread shadow experiment</h1><p>""" + e(payload.get("symbol")) + """ · """ + e(payload.get("start")) + """ through """ +e(payload.get("end"))+"""</p>
<div class="notice"><b>Research only — not approved for live trading</b>
<ul>"""+notes+"""</ul></div>
<h2>Five frozen replay scenarios</h2><p>Both modeled spreads and absolute
limits are displayed. A zero-trade result is not breakeven trading.</p>
"""+table(["Scenario","Spread (pts)","Cap (pts)","Trades","Net gain","PF","Max DD %","Gap-free P&L","Gap-free trades"],summary)+"""
<h2>Matched-spread new-trade cohorts</h2><p>Each comparison holds spread and
slippage identical, and varies only the research-only absolute entry spread cap.
The newly admitted cohort is path-dependent and must not be interpreted as
additive profit relative to control.</p>
"""+table(["Spread","Strict cap","Research cap","New trades","New P&L","New PF","New max DD %","Gap-free new trades","Gap-free new P&L","Displaced controls"],additional)+"""
<h2>Train / validation / previously inspected test</h2>
"""+table(["Scenario","Period","Trades","Net gain","PF","Closed-trade DD %","Gap-affected trades"],period_rows)+"""
<h2>Pipeline rejection reasons</h2>
"""+table(["Scenario","Stage","Reason","Count"],filter_rows)+"""
</body></html>"""


def main():
    p=argparse.ArgumentParser(description="Render offline research-only shadow dashboard")
    p.add_argument("file")
    args=p.parse_args()
    print(render(json.loads(Path(args.file).read_text(encoding="utf-8"))))


if __name__=="__main__":
    main()
