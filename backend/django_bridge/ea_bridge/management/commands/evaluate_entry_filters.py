"""Offline descriptive comparisons, not live promotion or unseen validation."""
import json
from types import SimpleNamespace
from django.core.management.base import BaseCommand, CommandError
from django.http import QueryDict
from ea_bridge.replay_backtest import _inputs, run_backtest_data
from ea_bridge.entry_filter_research import PLAN
from .evaluate_research_v8 import windows

def summarize(rows):
    valid=[r for r in rows if "metrics" in r]
    paired=[r for r in valid if r["delta_vs_baseline"] is not None]
    n=sum(r["metrics"]["closed_trades"] for r in valid)
    wins=sum(r["metrics"]["wins"] for r in valid)
    exits=[x for r in valid for x in r["metrics"]["trade_diagnostics"]["by_exit"]]
    gp=sum(x["gross_profit"] for x in exits)
    gl=sum(abs(x["gross_loss"]) for x in exits)
    return dict(windows=len(rows),successful_windows=len(valid),paired_windows=len(paired),
        closed_trades=n,win_rate=100*wins/n if n else None,
        pooled_profit_factor=gp/gl if gl else None,
        net_pnl=round(sum(r["metrics"]["net_gain"] for r in valid),2) if valid else None,
        paired_delta=round(sum(r["delta_vs_baseline"] for r in paired),2) if paired else None,
        profitable_windows=sum(r["metrics"]["net_gain"]>0 for r in valid),
        improved_windows=sum(r["delta_vs_baseline"]>0 for r in paired),
        active_windows=sum(r["metrics"]["closed_trades"]>0 for r in valid),
        worst_window_closed_drawdown_pct=max((r["metrics"]["max_drawdown_pct"] for r in valid),default=None),
        filter_rejections=sum(sum(r["metrics"]["entry_filter_rejections"].values()) for r in valid),
        open_at_boundary_windows=sum(bool(r["metrics"]["open_position"]) for r in valid),
        history_warning_windows=sum(r["data_quality"]["validation_status"]=="DESCRIPTIVE_ONLY_HISTORY_QUALITY_WARNING" for r in valid))

class Command(BaseCommand):
    help="V15.2.4 independent matched entry-filter replays"
    def add_arguments(self,p):
        for k in ("source","symbol","start","end"):p.add_argument("--"+k,required=True)
        for k in ("balance","point","tick-size","tick-value","volume-min","volume-step","volume-max","slippage-points","commission-per-lot"):
            p.add_argument("--"+k,required=True,type=float)
        p.add_argument("--spreads",nargs="+",type=float,choices=(42.,60.,80.),default=[42.,60.,80.])
        p.add_argument("--strategies",nargs="+",choices=list(PLAN),default=list(PLAN))
        p.add_argument("--window-days",type=int,default=90)
        p.add_argument("--max-candles",type=int,default=30000)
    def handle(self,*args,**opts):
        if not 7<=opts["window_days"]<=120 or not 258<=opts["max_candles"]<=100000:raise CommandError("Invalid window or candle limit")
        keys=("source","symbol","start","end","balance","point","tick_size","tick_value","volume_min","volume_step","volume_max","slippage_points","commission_per_lot")
        q=QueryDict(mutable=True)
        for k in keys:q[k]=str(opts[k])
        q["spread_points"]="42"
        data=_inputs(SimpleNamespace(GET=q))
        reports=[]; summaries=[]
        setups=list(dict.fromkeys(opts["strategies"]))
        spreads=list(dict.fromkeys(opts["spreads"]))
        for start,end in windows(data["start"],data["end"],opts["window_days"]):
            fingerprint=None
            for setup in setups:
                risk,filters=PLAN[setup]
                for spread in spreads:
                    baseline=None
                    for name in filters:
                        self.stderr.write(f"{start}..{end} {setup} spread={spread} filter={name}")
                        row=dict(start=str(start),end=str(end),strategy=setup,risk_percent=risk,spread_points=spread,entry_filter=name)
                        response=run_backtest_data(dict(data,start=start,end=end,setups=[setup],spread_points=spread,
                            risk_percent=risk,isolated_risk_sensitivity=True,all_strategy_risk_research=True,
                            v15_exit_policy="baseline",v15_entry_filter=name),max_candles=opts["max_candles"])
                        body=json.loads(response.content)
                        if response.status_code!=200:
                            reports.append(dict(row,error=body.get("error"),data_quality=body.get("data_quality")));continue
                        quality=body["data_quality"]
                        f=quality["history_fingerprint"]
                        if fingerprint is None:fingerprint=f
                        if f!=fingerprint:raise CommandError("History changed during window")
                        stats=body["strategies"][0]
                        stats.pop("trades")
                        if name=="baseline":baseline=stats["net_gain"]
                        reports.append(dict(row,metrics=stats,data_quality=quality,delta_vs_baseline=round(stats["net_gain"]-baseline,2) if baseline is not None else None))
        for setup in setups:
            for spread in spreads:
                for name in PLAN[setup][1]:
                    group=sorted((r for r in reports if r["strategy"]==setup and r["spread_points"]==spread and r["entry_filter"]==name),key=lambda r:r["start"])
                    split=len(group)*2//3
                    summaries.append(dict(strategy=setup,spread_points=spread,risk_percent=PLAN[setup][0],entry_filter=name,
                        overall=summarize(group),earlier=summarize(group[:split]),later=summarize(group[split:])))
        successful=sum("metrics" in r for r in reports)
        self.stdout.write(json.dumps(dict(version="V15.2.4",mode="OFFLINE_DESCRIPTIVE_ONLY",affects_orders=False,live_promotion=False,
            status="COMPLETE" if reports and successful==len(reports) else "PARTIAL" if successful else "NO_SUCCESSFUL_RUNS",
            attempted=len(reports),successful=successful,assumptions={**{k:opts[k] for k in keys},"window_days":opts["window_days"],"max_candles":opts["max_candles"],"spreads":spreads},
            plan={s:PLAN[s] for s in setups},summaries=summaries,windows=reports,
            limitations=["Filters are selected on previously studied data; earlier/later split is not unseen validation.",
                "Normal pipeline approval and minimum volume remain mandatory. Filtered signals are consumed by existing pipeline deduplication.",
                "UTC filters use signal decision time, not actual fill time. Stop/ATR uses original signal stop distance, not a modified stop.",
                "Fixed spread changes both costs and eligibility. Independent balances, closed-only drawdown, boundary positions excluded.",
                "No live promotion. Fast Scalp and four other M15 strategies outside focused suite."]),indent=2,allow_nan=False))
