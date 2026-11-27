"""Forward-only quote sampling. No broker actions or live setting writes."""
import math
from collections import Counter
from .entry_filter_research import filter_reason
from .replay_backtest import _research_pipeline, _indicators

PROFILES={
    "trend_baseline":("TREND_PULLBACK",.5,"baseline"),
    "trend_utc06_12":("TREND_PULLBACK",.5,"utc06_12"),
    "volatility_baseline":("VOLATILITY_COMPRESSION_EXPANSION",1.,"baseline"),
    "volatility_stop2_25":("VOLATILITY_COMPRESSION_EXPANSION",1.,"stop2_25"),
    "session_baseline":("SESSION_OPENING_RANGE_BREAKOUT",.5,"baseline"),
    "session_utc06_18":("SESSION_OPENING_RANGE_BREAKOUT",.5,"utc06_18"),
}
VERSION="V15.3"

def new_state(config, now):
    return dict(version=VERSION,mode="PAPER_ONLY",affects_orders=False,live_promotion=False,
        config=config,profiles={k:list(v) for k,v in PROFILES.items()},started_at=now,
        last_bar=None,last_quote=None,quote_samples=0,spread_sum=0,spread_max=0,
        missed_quote_intervals=0,missing_bar_intervals=0,
        books={k:dict(balance=config["balance"],peak=config["balance"],drawdown=0,
            marked_peak=config["balance"],marked_drawdown=0,position=None,trades=[],rejections={},evaluations=0)
            for k in PROFILES})

def reject(book, reason):
    book["last_reason"]=reason
    book["rejections"][reason]=book["rejections"].get(reason,0)+1

def paper_entry(book,result,quote,config,risk):
    p=result["stages"]["risk_management"]
    side=result["action"]; sign=1 if side=="BUY" else -1
    stop=float(p["stop_price"]); target=float(p.get("target_price") or p["take_profit_price"])
    volume=float(p["volume"])
    if not all(math.isfinite(x) and x>0 for x in (stop,target,volume)):
        return "INVALID_PLAN"
    point=config["point"]; slip=config["slippage_points"]*point
    value=config["tick_value"]/config["tick_size"]
    entry=(quote["ask"] if sign==1 else quote["bid"])+sign*slip
    mark=quote["bid"] if sign==1 else quote["ask"]
    distance=sign*(entry-stop); reward=sign*(target-entry)
    budget=book["balance"]*risk/100
    loss=((distance+slip)*value+config["commission_per_lot"])*volume
    if (not config["volume_min"]<=volume<=config["volume_max"] or
        abs(volume/config["volume_step"]-round(volume/config["volume_step"]))>1e-7 or
        distance<=0 or sign*(mark-stop)<=0 or reward<1.2*distance or loss>budget+1e-8):
        return "PAPER_FILL_RISK_OR_REWARD_REJECTED"
    book["position"]=dict(side=side,entry=entry,stop=stop,target=target,volume=volume,
        opened_at=quote["time"],entry_spread_points=(quote["ask"]-quote["bid"])/point,
        modeled_initial_loss=loss,modeled_entry_slippage_points=config["slippage_points"])
    return "PAPER_OPEN"

def observe_position(book,quote,config):
    p=book["position"]
    if not p:return
    sign=1 if p["side"]=="BUY" else -1
    mark=quote["bid"] if sign==1 else quote["ask"]
    stop_hit=sign*(mark-p["stop"])<=0
    target_hit=sign*(mark-p["target"])>=0
    slip=config["slippage_points"]*config["point"]
    value=config["tick_value"]/config["tick_size"]
    if stop_hit or target_hit:
        # No optimistic price improvement beyond a target. Stop may gap adversely.
        exit_price=mark-sign*slip if stop_hit else p["target"]-sign*slip
        pnl=(exit_price-p["entry"])*sign*value*p["volume"]-config["commission_per_lot"]*p["volume"]
        book["trades"].append(dict(p,exit=exit_price,closed_at=quote["time"],pnl=pnl,
            reason="STOP" if stop_hit else "TARGET",exit_spread_points=(quote["ask"]-quote["bid"])/config["point"]))
        book["balance"]+=pnl
        book["position"]=None
        book["peak"]=max(book["peak"],book["balance"])
        book["drawdown"]=max(book["drawdown"],100*(book["peak"]-book["balance"])/book["peak"])
        book["last_reason"]="PAPER_CLOSED"
    marked=book["balance"]
    if book["position"]:
        marked+=((mark-sign*slip-p["entry"])*sign*value-config["commission_per_lot"])*p["volume"]
    book["marked_equity"]=marked
    book["marked_peak"]=max(book["marked_peak"],marked)
    book["marked_drawdown"]=max(book["marked_drawdown"],100*(book["marked_peak"]-marked)/book["marked_peak"])

def cycle(state,rows,quote,now):
    config=state["config"]
    if quote is None:return "QUOTE_UNAVAILABLE"
    stamp=quote["time"]
    if not all(math.isfinite(quote[k]) for k in ("time","bid","ask")) or not 0<quote["bid"]<=quote["ask"] or not -5<=now-stamp<=30:
        return "QUOTE_STALE_OR_INVALID"
    previous=state["last_quote"]
    if previous is not None and stamp<=previous:return "WAIT_NEW_QUOTE"
    if previous is not None and stamp-previous>30:
        state["missed_quote_intervals"]+=1
        if any(b["position"] for b in state["books"].values()):
            state["exposure_gap"]=True
    state["last_quote"]=stamp
    spread=(quote["ask"]-quote["bid"])/config["point"]
    state["quote_samples"]+=1; state["spread_sum"]+=spread
    state["spread_max"]=max(state["spread_max"],spread)
    for book in state["books"].values():observe_position(book,quote,config)
    if len(rows)<256:return "WARMUP"
    bar=rows[-1]; t=bar["time"]
    if t%900 or not 0<=now-t-900<=60 or stamp<t+900:return "WAIT_FRESH_CLOSED_BAR"
    if state["last_bar"] is None:
        state["last_bar"]=t
        return "INITIALIZED_NO_BACKFILL"
    if t<=state["last_bar"]:return "WAIT_NEW_BAR"
    if t-state["last_bar"]!=900:
        state["missing_bar_intervals"]+=1
        state["last_bar"]=t
        return "BAR_GAP_SKIPPED"
    state["last_bar"]=t
    if any(b["time"]%900 or not (0<b["low"]<=min(b["open"],b["close"])<=max(b["open"],b["close"])<=b["high"]) for b in rows):
        return "INVALID_HISTORY"
    atr,fast,slow=_indicators(rows)
    if not math.isfinite(atr) or atr<=0:return "INVALID_ATR"
    point=config["point"]; value=config["tick_value"]/config["tick_size"]
    for key,(setup,risk,policy) in PROFILES.items():
        book=state["books"][key]
        if book["position"] or book["balance"]<=0:continue
        book["evaluations"]+=1
        if state.get("exposure_gap"):
            reject(book,"EXPOSURE_QUOTE_GAP_REVIEW_REQUIRED");continue
        if spread>config["max_spread_points"]:
            reject(book,"PAPER_SPREAD_CAP");continue
        pipeline=_research_pipeline(setup,risk_percent=risk,isolated_risk_sensitivity=True,all_strategy_risk_research=True)
        close=bar["close"]
        market=dict(symbol=config["symbol"],timeframe="M15",decision_time=t+900,bars=rows,
            last_bar_closed=True,latest_bar_time=t,bid=quote["bid"],ask=quote["ask"],point=point,
            trend="BUY" if fast>slow else "SELL" if fast<slow else "HOLD",
            setup_score=min(1,abs(fast-slow)/(atr*.8)),entry_quality=min(1,abs(close-rows[-5]["close"])/(atr*1.5)),
            spread_points=spread,account_balance=book["balance"],risk_percent=risk,
            stop_loss_points=atr*1.5/point,point_value_per_lot=value*point,
            volume_step=config["volume_step"],min_volume=config["volume_min"],max_volume=config["volume_max"],
            stop_level_points=config["stop_level_points"],take_profit_points=2*atr/point,
            target_reward_risk=pipeline.risk_manager.config.target_reward_risk,open_risk_percent=0)
        try:
            result=pipeline.run(market)
            if not result.get("approved") or result.get("action") not in ("BUY","SELL"):
                reject(book,str(result.get("reason") or "NOT_APPROVED"));continue
            reason=filter_reason(policy,result,market)
            if reason:reject(book,reason);continue
            reason=paper_entry(book,result,quote,config,risk)
            if reason=="PAPER_OPEN":
                book["last_reason"]=reason
                observe_position(book,quote,config)
            else:reject(book,reason)
        except (ValueError,TypeError,KeyError,ArithmeticError) as exc:
            reject(book,"EVALUATION_ERROR_"+type(exc).__name__)
    return "PROCESSED"

def metrics(state):
    for key,book in state["books"].items():
        trades=book["trades"]; gains=sum(max(0,t["pnl"]) for t in trades); losses=sum(max(0,-t["pnl"]) for t in trades)
        wins=sum(t["pnl"]>0 for t in trades)
        book["metrics"]=dict(closed_trades=len(trades),wins=wins,win_rate=100*wins/len(trades) if trades else None,
            profit_factor=gains/losses if losses else None,net_pnl=book["balance"]-state["config"]["balance"],
            closed_drawdown_pct=book["drawdown"],sampled_equity_drawdown_pct=book["marked_drawdown"],
            open_position=book["position"] is not None)
    state["comparisons"]={k:state["books"][k]["metrics"]["net_pnl"]-state["books"][k.split("_")[0]+"_baseline"]["metrics"]["net_pnl"]
        for k in PROFILES if not k.endswith("_baseline")}
    state["mean_observed_spread_points"]=state["spread_sum"]/state["quote_samples"] if state["quote_samples"] else None
    state["limitations"]=["Sampled quotes only; unobserved SL/TP touches may be missed. Not broker fills.",
        "Slippage and commission are modeled, not measured. No shared portfolio budget or actual margin.",
        "Pipelines reconstructed each decision; dedup state is not persistent as in historical replay.",
        "Exposure quote gaps latch new entries off; existing positions settle only on observed quotes. Start a new cohort after review.",
        "Closed-bar gaps skip decisions; no historical backfill. Research profiles are fixed for the cohort."]
