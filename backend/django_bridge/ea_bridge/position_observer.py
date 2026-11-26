"""Read-only position management observer; no command creation or live policy changes."""
from datetime import timedelta
from math import floor, ceil
from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from .models import EAHeartbeat, MarketBar
from .account_scope import real_cent_scope
from .strategy_registry import STRATEGIES, owned_strategy_position
from .scalper_engine import owned_position
from .execution_observer import number

def hypotheses(side, entry, initial_stop, stop, target, bid, ask, tick, minimum_distance, atr=None):
    sign=1 if side=="BUY" else -1
    quote=bid if sign==1 else ask
    risk=sign*(entry-initial_stop)
    if risk<=0:
        return {"status":"INVALID_INITIAL_RISK"}
    current_r=sign*(quote-entry)/risk
    def candidate(rule, value, triggered):
        proposed=(floor(value/tick+1e-9) if sign==1 else ceil(value/tick-1e-9))*tick
        if not triggered: state="WAIT"
        elif sign*(proposed-stop)<=1e-9: state="ALREADY_PROTECTED"
        elif sign*(quote-proposed)<=max(minimum_distance,tick): state="BROKER_DISTANCE"
        elif sign*(target-proposed)<=0: state="INVALID_TARGET"
        else: state="HYPOTHETICAL_TIGHTEN"
        return dict(rule=rule,proposed_stop=proposed,status=state)
    rules=[candidate("BREAKEVEN_"+str(r)+"R",entry,current_r>=r) for r in (.5,1,1.5)]
    if atr:
        rules.extend(candidate("TRAIL_"+str(m)+"ATR",quote-sign*m*atr,current_r>=1) for m in (1,1.5,2))
    remaining=sign*(target-quote)
    downside=sign*(quote-stop)
    return dict(status="OBSERVED",current_r=current_r,initial_risk_price=risk,
                executable_quote=quote,remaining_reward_risk=remaining/downside if remaining>0 and downside>0 else None,
                hypotheses=rules)

def candle_context(feed, timeframe, now):
    minutes=1 if timeframe=="M1" else 15
    step=timedelta(minutes=minutes)
    bars=list(MarketBar.objects.filter(source=feed.instance_id,symbol=feed.symbol,timeframe=timeframe,
        timestamp__lte=now-step).order_by("-timestamp")[:15])[::-1]
    if len(bars)!=15 or now-bars[-1].timestamp>step*2 or any(b.timestamp-a.timestamp!=step for a,b in zip(bars,bars[1:])):
        return None
    ranges=[max(float(b.high-b.low),abs(float(b.high-a.close)),abs(float(b.low-a.close))) for a,b in zip(bars,bars[1:])]
    atr=sum(ranges)/14
    return dict(atr=atr if atr>0 else None,bar_time=bars[-1].timestamp.isoformat(),
                method="SMA_TRUE_RANGE_14",last_close=float(bars[-1].close))

def build_report(now=None):
    now=now or timezone.now()
    result=dict(version="V15.1",mode="OBSERVATION_ONLY",affects_orders=False,generated_at=now.isoformat(),positions=[],
        limitations=["Snapshot only: no persistent MFE/MAE or reconstructed trade path.",
        "Holding duration uses command acknowledgement, not broker opening timestamp.",
        "ATR is simple mean true range on 14 contiguous closed candles.",
        "Trailing candidates activate at +1R; hypotheses are not validated trading policies.",
        "No hypothetical exit is executed; existing live protection is unchanged."])
    feed=EAHeartbeat.objects.filter(instance_id=getattr(settings,"RAMON_REAL_CENT_INSTANCE","")).first()
    ok,reason=real_cent_scope(feed)
    if not ok:return dict(result,status=reason)
    p=feed.payload
    stamp=parse_datetime(str(p.get("quote_time","")))
    if not feed.terminal_connected or feed.status!="TELEMETRY_OK" or not 0<=(now-feed.last_seen).total_seconds()<=60 or not stamp or not stamp.tzinfo or not -5<=(now-stamp).total_seconds()<=30:
        return dict(result,status="TELEMETRY_NOT_READY")
    if type(p.get("account_margin_mode")) is not int or p["account_margin_mode"]!=2 or p.get("execution_profile")!="ticket-magic-v1":
        return dict(result,status="OWNERSHIP_PROFILE_UNVERIFIED")
    q=p.get("quote",{}); spec=p.get("symbol_info",{})
    if not isinstance(q,dict) or not isinstance(spec,dict):return dict(result,status="INVALID_QUOTE")
    bid,ask,point=[number(q.get(k),positive=True) for k in ("bid","ask","point")]
    tick=number(spec.get("tick_size"),positive=True)
    stops=number(spec.get("stops_level_points"))
    if not all((bid,ask,point,tick)) or ask<bid or stops is None or stops<0:return dict(result,status="INVALID_QUOTE")
    positions=p.get("positions")
    if not isinstance(positions,list):return dict(result,status="INVALID_POSITIONS")
    contexts={}
    for pos in positions:
        row=dict(ticket=str(pos.get("ticket","")) if isinstance(pos,dict) else "",status="UNVERIFIED_POSITION")
        result["positions"].append(row)
        if not isinstance(pos,dict):continue
        c=owned_strategy_position(feed,pos) or owned_position(feed,pos)
        if not c:continue
        payload=c.payload
        strategy=payload.get("strategy_setup") if payload.get("engine")=="STRATEGY" else "FAST_SCALP"
        if strategy not in (*STRATEGIES,"FAST_SCALP"):continue
        side=payload.get("side")
        if side not in ("BUY","SELL") or type(pos.get("type")) is not int or pos["type"]!=(0 if side=="BUY" else 1):continue
        entry,stop,target,volume=[number(pos.get(k),positive=True) for k in ("price_open","sl","tp","volume")]
        initial=number(payload.get("stop_loss"),positive=True)
        original_volume=number(payload.get("volume"),positive=True)
        original_target=number(payload.get("take_profit"),positive=True)
        if not all((entry,stop,target,volume,initial,original_volume,original_target)) or abs(volume-original_volume)>1e-9 or abs(target-original_target)>tick:continue
        tf="M1" if strategy=="FAST_SCALP" else "M15"
        if tf not in contexts:contexts[tf]=candle_context(feed,tf,now)
        ctx=contexts[tf]
        row.update(strategy=strategy,side=side,command_id=c.command_id,entry=entry,current_stop=stop,target=target,
            timeframe=tf,candle_context=ctx,holding_seconds=(now-c.completed_at).total_seconds() if c.completed_at and c.completed_at<=now else None)
        row.update(hypotheses(side,entry,initial,stop,target,bid,ask,tick,stops*point,ctx["atr"] if ctx else None))
    return dict(result,status="READY",symbol=feed.symbol,server=feed.server_name,source=feed.instance_id,
        strategies=[dict(strategy=s,observed=sum(r.get("strategy")==s and r["status"]=="OBSERVED" for r in result["positions"])) for s in (*STRATEGIES,"FAST_SCALP")])
