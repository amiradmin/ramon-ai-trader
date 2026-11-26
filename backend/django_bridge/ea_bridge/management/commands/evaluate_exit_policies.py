"""Offline comparisons of fixed exit policies with a matched V15 baseline."""
import json
from statistics import mean
from types import SimpleNamespace
from django.core.management.base import BaseCommand, CommandError
from django.http import QueryDict
from ea_bridge.exit_research import POLICIES
from ea_bridge.replay_backtest import _inputs, run_backtest_data
from ea_bridge.strategy_registry import STRATEGIES
from .evaluate_research_v8 import windows

class Command(BaseCommand):
    help="Compare fixed exit policies across disjoint M15 research windows; no live writes."

    def add_arguments(self,p):
        for k in ("source","symbol","start","end"):
            p.add_argument("--"+k,required=True)
        for k in ("balance","point","tick-size","tick-value","volume-min","volume-step","volume-max","spread-points","slippage-points","commission-per-lot"):
            p.add_argument("--"+k,required=True,type=float)
        p.add_argument("--risk-percent",type=float,default=.5,choices=(.5,1.,1.5))
        p.add_argument("--window-days",type=int,default=90)
        p.add_argument("--minimum-trades",type=int,default=30)
        p.add_argument("--strategies",nargs="+",choices=list(STRATEGIES),default=list(STRATEGIES))
        p.add_argument("--policies",nargs="+",choices=list(POLICIES),default=list(POLICIES))

    def handle(self,*args,**opts):
        if not 7<=opts["window_days"]<=120 or opts["minimum_trades"]<1:
            raise CommandError("Invalid window or minimum trade count")
        names=list(dict.fromkeys(["baseline",*opts["policies"]]))
        setups=list(dict.fromkeys(opts["strategies"]))
        q=QueryDict(mutable=True)
        for k in ("source","symbol","start","end","balance","point","tick_size","tick_value","volume_min","volume_step","volume_max","spread_points","slippage_points","commission_per_lot"):
            q[k]=str(opts[k])
        try:data=_inputs(SimpleNamespace(GET=q))
        except ValueError as e:raise CommandError(str(e)) from e
        reports=[]
        for start,end in windows(data["start"],data["end"],opts["window_days"]):
            fingerprint=None
            for setup in setups:
                baseline=None
                for policy in names:
                    self.stderr.write(f"{start}..{end} {setup} {policy}")
                    response=run_backtest_data(dict(data,start=start,end=end,setups=[setup],
                        risk_percent=opts["risk_percent"],isolated_risk_sensitivity=True,
                        all_strategy_risk_research=True,v15_exit_policy=policy))
                    body=json.loads(response.content)
                    row=dict(start=str(start),end=str(end),strategy=setup,policy=policy)
                    if response.status_code!=200:
                        reports.append(dict(row,error=body.get("error"),data_quality=body.get("data_quality")))
                        continue
                    quality=body["data_quality"]
                    f=quality["history_fingerprint"]
                    if fingerprint is None:fingerprint=f
                    if fingerprint!=f:raise CommandError("History changed during window")
                    stats=body["strategies"][0]
                    trades=stats.pop("trades")
                    stats["mean_held_bars"]=mean(t["held_bars"] for t in trades) if trades else None
                    stats["mean_observed_mfe_r_lower_bound"]=mean(t["observed_mfe_r_lower_bound"] for t in trades) if trades else None
                    stats["mean_observed_mae_r_signed_upper_bound"]=mean(t["observed_mae_r_signed_upper_bound"] for t in trades) if trades else None
                    stats["minimum_trades_met"]=len(trades)>=opts["minimum_trades"]
                    if policy=="baseline":baseline=stats["net_gain"]
                    reports.append(dict(row,metrics=stats,delta_vs_baseline=round(stats["net_gain"]-baseline,2) if baseline is not None else None,data_quality=quality))
        summaries=[]
        for setup in setups:
            for policy in names:
                group=[r for r in reports if r["strategy"]==setup and r["policy"]==policy]
                valid=[r for r in group if "metrics" in r]
                paired=[r for r in valid if r["delta_vs_baseline"] is not None]
                summaries.append(dict(strategy=setup,policy=policy,windows=len(group),successful_windows=len(valid),
                    paired_windows=len(paired),closed_trades=sum(r["metrics"]["closed_trades"] for r in valid),
                    windows_meeting_trade_count=sum(r["metrics"]["minimum_trades_met"] for r in valid),
                    profitable_windows=sum(r["metrics"]["net_gain"]>0 for r in valid),
                    improved_windows=sum(r["delta_vs_baseline"]>0 for r in paired),
                    summed_independent_window_pnl=round(sum(r["metrics"]["net_gain"] for r in valid),2) if valid else None,
                    paired_delta=round(sum(r["delta_vs_baseline"] for r in paired),2) if paired else None,
                    worst_window_closed_drawdown_pct=max((r["metrics"]["max_drawdown_pct"] for r in valid),default=None)))
        self.stdout.write(json.dumps(dict(version="V15.2",mode="OFFLINE_DESCRIPTIVE_ONLY",affects_orders=False,live_promotion=False,
            source=data["source"],symbol=data["symbol"],risk_percent=opts["risk_percent"],
            assumptions={k:data[k] for k in ("balance","point","tick_size","tick_value","volume_min","volume_step","volume_max","spread_points","slippage_points","commission_per_lot")},
            policies={n:POLICIES[n] for n in names},windows=reports,summaries=summaries,
            limitations=["Seven M15 strategies only; Fast Scalp uses a separate M1/M5 engine and is not compared here.",
                "Same entry logic, but exit timing changes subsequent opportunity availability and balance-based sizing.",
                "Next-open entries skip gaps; stops use adverse gap opening prices; fixed spread and costs, OHLC stop-first ambiguity.",
                "Stops change after completed candles; discretionary exits fill next available open; terminal excursions exclude ambiguous intrabar extremes.",
                "Independent windows reset balances and positions; open positions at boundaries excluded; closed drawdown only.",
                "No tick execution, historical news, broker stop distances or live promotion. Previously studied periods are not unseen holdout.",
                "Remaining-RR exit is an early-exit hypothesis, not adaptive target extension." ]),indent=2,allow_nan=False))
