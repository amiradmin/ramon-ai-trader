"""Forward-only paper research. No broker commands, no live configuration writes."""
import argparse
import fcntl
import json
import math
import os
import time
from pathlib import Path
from datetime import timedelta
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from ea_bridge.models import MarketBar
from ea_bridge.replay_backtest import _research_pipeline, _indicators

POLICIES = {"VOLATILITY_COMPRESSION_EXPANSION": 1.0,
            "SESSION_OPENING_RANGE_BREAKOUT": 0.5}

def settle(position, bar, spread, slip, value, commission):
    buy = position["side"] == "BUY"
    offset = 0 if buy else spread
    stop, target = position["stop"], position["target"]
    if buy:
        stop_hit, target_hit = bar["low"] <= stop, bar["high"] >= target
    else:
        stop_hit, target_hit = bar["high"]+offset >= stop, bar["low"]+offset <= target
    if not (stop_hit or target_hit):
        return None
    price = (min(stop,bar["open"]) - slip if buy else max(stop,bar["open"]+offset)+slip) if stop_hit else target
    pnl = (price-position["entry"])*(1 if buy else -1)*value*position["volume"]-commission*position["volume"]
    return dict(position, exit=price, closed_at=bar["time"], pnl=pnl, reason="STOP" if stop_hit else "TARGET")

def save(path,state):
    tmp=path.with_suffix(path.suffix+".tmp")
    with tmp.open("w") as stream:
        json.dump(state,stream,indent=2,allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp,path)

def row_dict(r):
    return dict(time=r.timestamp.timestamp(),open=float(r.open),high=float(r.high),
                low=float(r.low),close=float(r.close),closed=True)

def cycle(state, rows, config, now):
    if len(rows)<128:
        return "WARMUP"
    bar=rows[-1]
    stamp=bar["time"]
    if not 0<=now-stamp-900<900 or stamp%900:
        return "STALE_OR_OFF_GRID"
    if state["last_bar"] is None:
        state["last_bar"]=stamp
        return "INITIALIZED_NO_BACKFILL"
    if stamp<=state["last_bar"]:
        return "WAIT_NEW_BAR"
    if stamp-state["last_bar"]!=900:
        # Fail closed: unknown price path cannot be silently treated as a fill.
        state["gap_detected"]=True
        return "GAP_MANUAL_REVIEW"
    if state.get("gap_detected"):
        return "GAP_MANUAL_REVIEW"
    point=config["point"]; spread=config["spread_points"]*point
    slip=config["slippage_points"]*point
    value=config["tick_value"]/config["tick_size"]
    for name,book in state["books"].items():
        pending=book.pop("pending",None)
        if pending:
            entry=bar["open"]+(spread+slip if pending["side"]=="BUY" else -slip)
            distance=(entry-pending["stop"])*(1 if pending["side"]=="BUY" else -1)
            reward=(pending["target"]-entry)*(1 if pending["side"]=="BUY" else -1)
            if distance>0 and reward>=1.2*distance and distance*value*pending["volume"]<=pending["budget"]:
                book["position"]=dict(pending,entry=entry,opened_at=stamp)
        if book.get("position"):
            trade=settle(book["position"],bar,spread,slip,value,config["commission_per_lot"])
            if trade:
                book["trades"].append(trade)
                book["balance"]+=trade["pnl"]
                book["position"]=None
                book["peak"]=max(book["peak"],book["balance"])
                book["drawdown"]=max(book["drawdown"],100*(book["peak"]-book["balance"])/book["peak"])
    atr,fast,slow=_indicators(rows)
    if not math.isfinite(atr) or atr<=0:
        state["last_bar"]=stamp
        return "INVALID_ATR"
    for name,risk in POLICIES.items():
        book=state["books"][name]
        if book.get("position") or book.get("pending") or book["balance"]<=0:
            continue
        pipeline=_research_pipeline(name,risk_percent=risk,isolated_risk_sensitivity=True,all_strategy_risk_research=True)
        close=bar["close"]
        market=dict(symbol=config["symbol"],timeframe="M15",decision_time=stamp+900,bars=rows,
            last_bar_closed=True,latest_bar_time=stamp,bid=close,ask=close+spread,point=point,
            trend="BUY" if fast>slow else "SELL" if fast<slow else "HOLD",
            setup_score=min(1,abs(fast-slow)/(atr*.8)),entry_quality=min(1,abs(close-rows[-5]["close"])/(atr*1.5)),
            spread_points=config["spread_points"],account_balance=book["balance"],risk_percent=risk,
            stop_loss_points=atr*1.5/point,point_value_per_lot=value*point,volume_step=config["volume_step"],
            min_volume=config["volume_min"],max_volume=config["volume_max"],stop_level_points=0,
            take_profit_points=2*atr/point,target_reward_risk=pipeline.risk_manager.config.target_reward_risk,
            open_risk_percent=0)
        result=pipeline.run(market)
        book["last_reason"]=result.get("reason")
        if result.get("approved") and result.get("action") in ("BUY","SELL"):
            plan=result["stages"]["risk_management"]
            stop=float(plan["stop_price"]); target=float(plan.get("target_price") or plan["take_profit_price"])
            volume=float(plan["volume"])
            if all(math.isfinite(x) and x>0 for x in (stop,target,volume)):
                book["pending"]=dict(side=result["action"],stop=stop,target=target,volume=volume,
                    budget=book["balance"]*risk/100,signal_at=stamp)
    state["last_bar"]=stamp
    return "PROCESSED"

class Command(BaseCommand):
    help="Run isolated M15 forward paper research in a persistent local JSON file."
    def add_arguments(self,p):
        for key in ("source","symbol","state-file"):
            p.add_argument("--"+key,required=True)
        for key in ("balance","point","tick-size","tick-value","volume-min","volume-step","volume-max"):
            p.add_argument("--"+key,required=True,type=float)
        for key,default in (("spread-points",42),("slippage-points",5),("commission-per-lot",5)):
            p.add_argument("--"+key,type=float,default=default)
        p.add_argument("--once",action="store_true")
        p.add_argument("--poll-seconds",type=int,default=15)
    def handle(self,*args,**opts):
        keys=("source","symbol","balance","point","tick_size","tick_value","volume_min","volume_step","volume_max",
              "spread_points","slippage_points","commission_per_lot")
        config={k:opts[k] for k in keys}
        for key in keys[2:]:
            v=config[key]
            if not math.isfinite(v) or v<0 or (key in keys[2:9] and v==0):
                raise CommandError("Invalid "+key)
        if opts["poll_seconds"]<5 or config["volume_min"]>config["volume_max"]:
            raise CommandError("Invalid poll interval or volume range")
        path=Path(opts["state_file"])
        with path.with_suffix(path.suffix+".lock").open("a") as lock:
            try:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                raise CommandError("Shadow state already locked")
            if path.exists():
                state=json.loads(path.read_text())
                if state["config"]!=config or state["policies"]!=POLICIES:
                    raise CommandError("Configuration changed; use a new state file")
            else:
                state=dict(mode="PAPER_ONLY",live_promotion=False,config=config,policies=POLICIES,last_bar=None,
                    books={n:dict(balance=config["balance"],peak=config["balance"],drawdown=0,trades=[],position=None)
                           for n in POLICIES})
            while True:
                now=timezone.now()
                rows=[row_dict(r) for r in MarketBar.objects.filter(source=config["source"],symbol=config["symbol"],
                    timeframe="M15",timestamp__lte=now-timedelta(minutes=15),received_at__lte=now).order_by("-timestamp")[:256]][::-1]
                status=cycle(state,rows,config,now.timestamp())
                state["status"]=status
                save(path,state)
                self.stdout.write(status)
                if opts["once"] or status=="GAP_MANUAL_REVIEW":
                    return
                time.sleep(opts["poll_seconds"])
