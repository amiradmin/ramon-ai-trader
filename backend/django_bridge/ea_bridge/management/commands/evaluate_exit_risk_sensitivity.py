"""Research only: reuse V15.2 runner without changing its execution model."""
import io
import json
from django.core.management.base import BaseCommand, CommandError
from django.core.management import call_command
from .evaluate_exit_policies import Command as ExitCommand

PLAN={
    "TREND_PULLBACK":("baseline","reversal"),
    "VOLATILITY_COMPRESSION_EXPANSION":("baseline","time_8"),
    "SESSION_OPENING_RANGE_BREAKOUT":("baseline","time_8","time_16","low_remaining_rr"),
}

def summarize(rows, minimum):
    valid=[r for r in rows if "metrics" in r]
    paired=[r for r in valid if r["delta_vs_baseline"] is not None]
    trades=sum(r["metrics"]["closed_trades"] for r in valid)
    wins=sum(r["metrics"]["wins"] for r in valid)
    gross_profit=gross_loss=0
    for r in valid:
        for x in r["metrics"]["trade_diagnostics"]["by_exit"]:
            gross_profit+=x["gross_profit"]
            gross_loss+=abs(x["gross_loss"])
    return dict(windows=len(rows),successful_windows=len(valid),paired_windows=len(paired),
        closed_trades=trades,win_rate=round(100*wins/trades,2) if trades else None,
        pooled_profit_factor=round(gross_profit/gross_loss,4) if gross_loss else None,
        net_pnl=round(sum(r["metrics"]["net_gain"] for r in valid),2) if valid else None,
        paired_delta=round(sum(r["delta_vs_baseline"] for r in paired),2) if paired else None,
        profitable_windows=sum(r["metrics"]["net_gain"]>0 for r in valid),
        improved_windows=sum(r["delta_vs_baseline"]>0 for r in paired),
        active_windows=sum(r["metrics"]["closed_trades"]>0 for r in valid),
        zero_trade_windows=sum(r["metrics"]["closed_trades"]==0 for r in valid),
        windows_meeting_minimum=sum(r["metrics"]["closed_trades"]>=minimum for r in valid),
        worst_window_closed_drawdown_pct=max((r["metrics"]["max_drawdown_pct"] for r in valid),default=None),
        minimum_lot_rejections=sum(x["count"] for r in valid for x in r["metrics"]["rejections"] if x["reason"]=="RISK_BUDGET_BELOW_MINIMUM_VOLUME"),
        evaluations=sum(r["metrics"]["evaluations"] for r in valid),
        history_warning_windows=sum(r["data_quality"]["validation_status"]!="PASS" for r in valid),
        open_at_boundary_windows=sum(bool(r["metrics"]["open_position"]) for r in valid))

class Command(BaseCommand):
    help="V15.2.2 descriptive risk sensitivity; no live settings or orders."
    def add_arguments(self,p):
        for k in ("source","symbol","start","end"):
            p.add_argument("--"+k,required=True)
        for k in ("balance","point","tick-size","tick-value","volume-min","volume-step","volume-max","spread-points","slippage-points","commission-per-lot"):
            p.add_argument("--"+k,required=True,type=float)
        p.add_argument("--window-days",type=int,default=90)
        p.add_argument("--max-candles",type=int,default=30000)
        p.add_argument("--minimum-trades",type=int,default=30)
        p.add_argument("--risks",nargs="+",type=float,choices=(.5,1.,1.5),default=[.5,1.,1.5])
        p.add_argument("--strategies",nargs="+",choices=list(PLAN),default=list(PLAN))

    def handle(self,*args,**opts):
        common={k:opts[k] for k in ("source","symbol","start","end","balance","point","tick_size","tick_value","volume_min","volume_step","volume_max","spread_points","slippage_points","commission_per_lot","window_days","max_candles","minimum_trades")}
        reports=[]; summaries=[]; fingerprints={}; expected_windows=None
        for strategy in dict.fromkeys(opts["strategies"]):
            for risk in dict.fromkeys(opts["risks"]):
                self.stderr.write(f"V15.2.2 {strategy} risk={risk}%")
                output=io.StringIO()
                call_command(ExitCommand(),**common,strategies=[strategy],policies=list(PLAN[strategy]),risk_percent=risk,stdout=output,stderr=self.stderr)
                report=json.loads(output.getvalue())
                rows=report["windows"]
                periods=sorted({(r["start"],r["end"]) for r in rows})
                if not periods:raise CommandError("No historical windows")
                if expected_windows is None:expected_windows=periods
                if periods!=expected_windows:raise CommandError("Window coverage changed")
                keys=[(r["start"],r["end"],r["policy"]) for r in rows]
                if len(keys)!=len(set(keys)) or set(keys)!={(a,b,p) for a,b in periods for p in PLAN[strategy]}:
                    raise CommandError("Unexpected policy/window coverage")
                for row in rows:
                    if "metrics" in row:
                        key=(row["start"],row["end"])
                        f=row["data_quality"]["history_fingerprint"]
                        if fingerprints.setdefault(key,f)!=f:raise CommandError("History changed across risk runs")
                    row["risk_percent"]=risk
                reports.extend(rows)
                for policy in PLAN[strategy]:
                    group=sorted((r for r in rows if r["policy"]==policy),key=lambda r:r["start"])
                    split=max(1,len(group)*2//3)
                    summaries.append(dict(strategy=strategy,policy=policy,risk_percent=risk,
                        overall=summarize(group,opts["minimum_trades"]),
                        earlier=summarize(group[:split],opts["minimum_trades"]),
                        later=summarize(group[split:],opts["minimum_trades"])))
        successful=sum("metrics" in r for r in reports)
        self.stdout.write(json.dumps(dict(version="V15.2.2",mode="OFFLINE_DESCRIPTIVE_ONLY",
            affects_orders=False,live_promotion=False,status="COMPLETE" if reports and successful==len(reports) else "PARTIAL" if successful else "NO_SUCCESSFUL_RUNS",
            attempted=len(reports),successful=successful,assumptions=common,
            plan={s:PLAN[s] for s in dict.fromkeys(opts["strategies"])},
            risks=list(dict.fromkeys(opts["risks"])),summaries=summaries,windows=reports,
            limitations=["Risk sensitivity is not permission to change live risk.",
                "Earlier/later split is descriptive, not unseen holdout; prior research used these dates.",
                "Independent balances reset each window; closed PnL and drawdown only, no portfolio aggregation.",
                "Fixed spread/costs, OHLC ambiguity, historical gaps, terminal excursion bounds and boundary exclusions inherited from V15.2.",
                "Minimum-lot rejection counts are evaluations, not unique missed profitable trades.",
                "Fast Scalp and four other M15 strategies excluded from this focused suite.",
                "No automated candidate promotion or statistical significance claim." ]),indent=2,allow_nan=False))
