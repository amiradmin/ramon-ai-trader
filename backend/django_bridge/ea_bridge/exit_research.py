"""Offline exit-policy hypotheses. No live command or policy integration."""
from math import floor, ceil

POLICIES={
    "baseline":{},
    **{"be_"+str(r):{"breakeven_r":r} for r in (.5,1,1.5)},
    **{"trail_"+str(m):{"trailing_atr":m} for m in (1,1.5,2)},
    "be_trail":{"breakeven_r":1,"trailing_atr":1.5},
    "time_8":{"time_exit":8},
    "time_16":{"time_exit":16},
    "reversal":{"reversal":True},
    "low_remaining_rr":{"remaining_rr":.5},
}

def next_stop(pos, mark, atr, tick, policy):
    sign=1 if pos["side"]=="BUY" else -1
    risk=sign*(pos["entry"]-pos["initial_stop"])
    old=pos["stop"]
    if risk<=0:return old
    r=sign*(mark-pos["entry"])/risk
    candidates=[old]
    trigger=policy.get("breakeven_r")
    if trigger and r>=trigger:candidates.append(pos["entry"])
    multiple=policy.get("trailing_atr")
    if multiple and atr and atr>0 and r>=1:candidates.append(mark-sign*multiple*atr)
    rounded=[(floor(x/tick+1e-9) if sign==1 else ceil(x/tick-1e-9))*tick for x in candidates[1:]]
    valid=[x for x in rounded if sign*(mark-x)>=tick-1e-9 and sign*(pos["target"]-x)>0]
    return (max if sign==1 else min)([old,*valid])

def close_signal(pos, bar, previous, mark, policy):
    """Evaluate completed candle; caller fills on next available open."""
    sign=1 if pos["side"]=="BUY" else -1
    if policy.get("time_exit") and pos["held_bars"]>=policy["time_exit"]:return "TIME_EXIT"
    if policy.get("reversal") and (bar["close"]<previous["low"] if sign==1 else bar["close"]>previous["high"]):
        return "REVERSAL_EXIT"
    downside=sign*(mark-pos["stop"])
    reward=sign*(pos["target"]-mark)
    if policy.get("remaining_rr") and sign*(mark-pos["entry"])>0 and downside>0 and 0<reward/downside<policy["remaining_rr"]:
        return "LOW_REMAINING_RR_EXIT"
    return None
