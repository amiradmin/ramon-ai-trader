"""Offline fixed-policy chronological evaluation. No live writes or promotion."""
import json
from datetime import date, timedelta
from types import SimpleNamespace
from django.core.management.base import BaseCommand, CommandError
from django.http import QueryDict
from ea_bridge.replay_backtest import _inputs, run_backtest_data
from ea_bridge.strategy_registry import STRATEGIES
from .evaluate_trend_pullback import BASE

V8 = {
    "m15_baseline": {"timeframe": "M15"},
    "m15_utc_06_24": {"timeframe": "M15", "entry_hours": [6,24]},
    "m5_sell": {"timeframe": "M5", "allowed_side": "SELL"},
    "m5_sell_m15": {"timeframe": "M5", "allowed_side": "SELL", "confirm_timeframes": ["M15"]},
    "m5_sell_h1": {"timeframe": "M5", "allowed_side": "SELL", "confirm_timeframes": ["H1"]},
    "m5_sell_both": {"timeframe": "M5", "allowed_side": "SELL", "confirm_timeframes": ["M15","H1"]},
    "m5_buy_both": {"timeframe": "M5", "allowed_side": "BUY", "confirm_timeframes": ["M15","H1"]},
}


def windows(start, end, days):
    if days < 1 or start > end:
        raise ValueError("Invalid windows")
    while start <= end:
        last = min(end, start + timedelta(days=days-1))
        yield start, last
        start = last + timedelta(days=1)


def scenarios(sensitivity):
    # One factor at a time; avoid interpreting a large grid as independent evidence.
    return [(42, 1.5)] + ([(s,1.5) for s in (30,60,80)] +
                           [(42,r) for r in (0.5,1.0)] if sensitivity else [])


def streak(trades):
    longest = current = 0
    for trade in trades:
        current = current+1 if trade["pnl"] < 0 else 0
        longest = max(longest,current)
    return longest


class Command(BaseCommand):
    help = "Frozen V8 and all-M15 independent research across disjoint chronological windows."

    def add_arguments(self, parser):
        for key in ("source","symbol","start","end"):
            parser.add_argument("--"+key, required=True)
        for key in ("balance","point","tick-size","tick-value","volume-min",
                    "volume-step","volume-max","slippage-points","commission-per-lot"):
            parser.add_argument("--"+key, required=True, type=float)
        parser.add_argument("--suite", choices=("v8","all-m15","v9"), default="v8")
        parser.add_argument("--window-days", type=int, default=60)
        parser.add_argument("--max-candles", type=int, default=30000)
        parser.add_argument("--sensitivity", action="store_true")
        parser.add_argument("--minimum-trades", type=int, default=30)

    def handle(self, *args, **opts):
        if not 7 <= opts["window_days"] <= 120 or not 130 <= opts["max_candles"] <= 100000 or opts["minimum_trades"] < 1:
            raise CommandError("Invalid window, candle or trade limit")
        if opts["suite"] in ("all-m15","v9") and opts["sensitivity"]:
            raise CommandError("Sensitivity is currently restricted to V8 Trend Pullback")
        q = QueryDict(mutable=True)
        for k in ("source","symbol","start","end","balance","point","tick_size","tick_value",
                  "volume_min","volume_step","volume_max","slippage_points","commission_per_lot"):
            q[k] = str(opts[k])
        q["spread_points"] = "42"
        q.setlist("setups", ["TREND_PULLBACK"])
        try:
            data = _inputs(SimpleNamespace(GET=q))
        except (ValueError, KeyError) as exc:
            raise CommandError(str(exc)) from exc
        policies = V8 if opts["suite"] == "v8" else {s: {"timeframe":"M15"} for s in STRATEGIES}
        costs = scenarios(opts["sensitivity"]) if opts["suite"] == "v8" else [(42,r) for r in (0.5,1.0,1.5)] if opts["suite"] == "v9" else [(42,0.5)]
        reports = []
        for first,last in windows(data["start"], data["end"], opts["window_days"]):
            fingerprints = {}
            for name, policy in policies.items():
                for spread,risk in costs:
                    self.stderr.write(f"{first}..{last} {name} spread={spread} risk={risk}: starting")
                    overrides = dict(data,start=first,end=last,spread_points=spread,risk_percent=risk)
                    if opts["suite"] == "v8":
                        overrides.update(opportunity_overrides=BASE, isolated_risk_sensitivity=True,
                            isolated_timeframe=policy["timeframe"], trend_research={"version":8,**policy})
                    else:
                        overrides["setups"] = [name]
                        if opts["suite"] == "v9":
                            overrides.update(isolated_risk_sensitivity=True, all_strategy_risk_research=True)
                    def progress(done,total):
                        if done == total or done % 1000 == 0:
                            self.stderr.write(f"  {done}/{total}")
                    response = run_backtest_data(overrides,max_candles=opts["max_candles"],progress=progress)
                    result = json.loads(response.content)
                    row = dict(start=str(first),end=str(last),candidate=name,spread_points=spread,risk_percent=risk,
                               timeframe=policy["timeframe"])
                    if response.status_code != 200:
                        reports.append(dict(row,error=result.get("error"),data_quality=result.get("data_quality")))
                        continue
                    quality = result["data_quality"]
                    fingerprint = quality["history_fingerprint"]
                    prior = fingerprints.setdefault(policy["timeframe"], fingerprint)
                    if prior != fingerprint:
                        raise CommandError("History changed within window/timeframe")
                    stats = result["strategies"][0]
                    trades = stats.pop("trades")
                    stats["longest_losing_streak"] = streak(trades)
                    stats["expectancy_per_closed_trade"] = round(stats["net_gain"]/len(trades),4) if trades else None
                    stats["minimum_volume_rejections"] = sum(x["count"] for x in stats["rejections"] if x["reason"] == "RISK_BUDGET_BELOW_MINIMUM_VOLUME")
                    stats["minimum_trade_count_met"] = len(trades) >= opts["minimum_trades"]
                    reports.append(dict(row,metrics=stats,data_quality=quality,assumptions=result["assumptions"]))
        summaries = []
        for name in policies:
            for spread,risk in costs:
                group = [r for r in reports if r["candidate"]==name and r["spread_points"]==spread and r["risk_percent"]==risk]
                valid = [r["metrics"] for r in group if "metrics" in r]
                summaries.append(dict(candidate=name,spread_points=spread,risk_percent=risk,
                    windows=len(group),successful_windows=len(valid),profitable_windows=sum(m["net_gain"]>0 for m in valid),
                    closed_trades=sum(m["closed_trades"] for m in valid),
                    minimum_volume_rejections=sum(m["minimum_volume_rejections"] for m in valid),
                    windows_meeting_trade_count=sum(m["minimum_trade_count_met"] for m in valid),
                    summed_independent_window_pnl=round(sum(m["net_gain"] for m in valid),2) if valid else None,
                    worst_window_closed_drawdown_pct=max((m["max_drawdown_pct"] for m in valid),default=None),
                    longest_within_window_losing_streak=max((m["longest_losing_streak"] for m in valid),default=None)))
        self.stdout.write(json.dumps(dict(mode="FROZEN_POLICY_DESCRIPTIVE_ONLY",live_promotion=False,
            research_version=9 if opts["suite"] == "v9" else 8,suite=opts["suite"],source=data["source"],symbol=data["symbol"],policies=policies,
            minimum_trades=opts["minimum_trades"],windows=reports,summaries=summaries,
            limitations="Fixed policies, no fitting or winner selection. Windows reset balances and positions; open positions at boundaries are not included in closed PnL. This is rolling evaluation, not retraining walk-forward or portfolio simulation. Previously examined dates are not fresh holdout. M5 requires same-source M1. Context warmup may be insufficient initially. Fixed costs and OHLC stop-first, closed drawdown only. All-M15 uses standard 0.5% cap; V9 uses isolated risk at 0.5/1/1.5%, both use identical legacy replay fills for comparability. V8 uses isolated risk and gap-aware fills. Missing windows are not zero-return windows."),indent=2))
