"""Isolated research: fixed baseline, no live configuration changes."""
import json
from types import SimpleNamespace
from django.core.management.base import BaseCommand, CommandError
from django.http import QueryDict
from ea_bridge.replay_backtest import _inputs, run_backtest_data
from .evaluate_research_v8 import windows

PLAN={"TREND_PULLBACK":.5,"VOLATILITY_COMPRESSION_EXPANSION":1.,"SESSION_OPENING_RANGE_BREAKOUT":.5}

class Command(BaseCommand):
    help="V15.2.3 entry diagnostics with fixed spread sensitivity"
    def add_arguments(self,p):
        for k in ("source","symbol","start","end"):p.add_argument("--"+k,required=True)
        for k in ("balance","point","tick-size","tick-value","volume-min","volume-step","volume-max","slippage-points","commission-per-lot"):
            p.add_argument("--"+k,required=True,type=float)
        p.add_argument("--spreads",nargs="+",type=float,choices=(42.,60.,80.),default=[42.,60.,80.])
        p.add_argument("--strategies",nargs="+",choices=list(PLAN),default=list(PLAN))
        p.add_argument("--window-days",type=int,default=90)
        p.add_argument("--max-candles",type=int,default=30000)
    def handle(self,*args,**opts):
        if not 7<=opts["window_days"]<=120 or not 130<=opts["max_candles"]<=100000:raise CommandError("Invalid window or candle limit")
        keys=("source","symbol","start","end","balance","point","tick_size","tick_value","volume_min","volume_step","volume_max","slippage_points","commission_per_lot")
        q=QueryDict(mutable=True)
        for k in keys:q[k]=str(opts[k])
        q["spread_points"]="42"
        data=_inputs(SimpleNamespace(GET=q))
        reports=[]; summaries=[]
        for start,end in windows(data["start"],data["end"],opts["window_days"]):
            fingerprint=None
            for setup in dict.fromkeys(opts["strategies"]):
                for spread in dict.fromkeys(opts["spreads"]):
                    self.stderr.write(f"{start}..{end} {setup} spread={spread}")
                    row=dict(start=str(start),end=str(end),strategy=setup,risk_percent=PLAN[setup],spread_points=spread)
                    response=run_backtest_data(dict(data,start=start,end=end,setups=[setup],spread_points=spread,
                        risk_percent=PLAN[setup],isolated_risk_sensitivity=True,all_strategy_risk_research=True,
                        v15_exit_policy="baseline",v15_entry_audit=True),max_candles=opts["max_candles"])
                    body=json.loads(response.content)
                    if response.status_code!=200:
                        reports.append(dict(row,error=body.get("error")));continue
                    quality=body["data_quality"]
                    f=quality["history_fingerprint"]
                    if fingerprint is None:fingerprint=f
                    if f!=fingerprint:raise CommandError("History changed during window")
                    stats=body["strategies"][0]
                    stats.pop("trades")
                    reports.append(dict(row,metrics=stats,data_quality=quality))
        for setup in dict.fromkeys(opts["strategies"]):
            for spread in dict.fromkeys(opts["spreads"]):
                group=[r for r in reports if r["strategy"]==setup and r["spread_points"]==spread]
                valid=[r for r in group if "metrics" in r]
                audits=[r["metrics"]["entry_quality_audit"]["rejected_volume"] for r in valid]
                resolved=sum(a["resolved"] for a in audits)
                summaries.append(dict(strategy=setup,risk_percent=PLAN[setup],spread_points=spread,
                    windows=len(group),successful_windows=len(valid),
                    net_pnl=round(sum(r["metrics"]["net_gain"] for r in valid),2) if valid else None,
                    closed_trades=sum(r["metrics"]["closed_trades"] for r in valid),
                    worst_window_closed_drawdown_pct=max((r["metrics"]["max_drawdown_pct"] for r in valid),default=None),
                    rejected_observations=sum(a["observations"] for a in audits),resolved=resolved,
                    positive=sum(a["positive"] for a in audits),
                    mean_net_r_resolved_only=sum(a["resolved"]*a["mean_net_r_resolved_only"] for a in audits if a["resolved"])/resolved if resolved else None))
        successful=sum("metrics" in r for r in reports)
        self.stdout.write(json.dumps(dict(version="V15.2.3",mode="OFFLINE_DESCRIPTIVE_ONLY",affects_orders=False,live_promotion=False,
            status="COMPLETE" if reports and successful==len(reports) else "PARTIAL" if successful else "NO_SUCCESSFUL_RUNS",
            attempted=len(reports),successful=successful,assumptions={k:opts[k] for k in keys},
            plan=PLAN,summaries=summaries,windows=reports,
            limitations=["No entry filters optimized or applied. Bucket results are observational and confounded.",
                "Minimum-volume rejection counterfactuals may overlap; not executable trades or evidence of missed profit.",
                "Configured-R target ignores later structural target validation; gaps and unresolved outcomes excluded from mean R.",
                "Fixed spread scenarios with constant slippage and commission, independent windows and closed-only drawdown.",
                "Risk levels fixed from previous descriptive research; not live recommendations. No unseen holdout." ]),indent=2,allow_nan=False))
