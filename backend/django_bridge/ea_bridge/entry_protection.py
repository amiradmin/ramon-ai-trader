"""Read-only entry observations. No order authorization or parameter changes."""
from math import isclose
from .execution_observer import number

def entry_report(payload, market, raw):
    observation=raw.get("entry_observation") if isinstance(raw,dict) else None
    if not isinstance(observation,dict) or type(observation.get("version")) is not int or observation["version"]!=1 or observation.get("mode")!="OBSERVATION_ONLY":
        return {"status":"UNAVAILABLE","warnings":[]}
    def pos(key):
        return number(observation.get(key),positive=True)
    def val(key):
        return number(observation.get(key))
    bid,ask,point=pos("bid"),pos("ask"),pos("point")
    tick_size=pos("tick_size")
    stops=val("stops_level_points")
    age=val("quote_age_ms")
    side=payload.get("side")
    sl=number(payload.get("stop_loss"),positive=True)
    tp=number(payload.get("take_profit"),positive=True)
    reserved=number(payload.get("reserved_risk_money"),positive=True)
    loss=val("stop_pnl_money")
    reward=val("target_pnl_money")
    warnings=[]; missing=[]
    out=dict(status="OBSERVED",warnings=warnings,missing=missing,quote_age_ms=age,
        bid=bid,ask=ask,point=point,tick_size=tick_size,stops_level_points=stops,
        stop_loss=sl,take_profit=tp,reserved_risk_money=reserved,estimated_loss_money=None,
        estimated_reward_money=None,reward_risk=None,spread_points=None,
        adverse_entry_points=None,adverse_entry_spread_ratio=None,stop_distance_points=None,
        target_distance_points=None)
    if age is None:missing.append("QUOTE_AGE")
    elif age < -1000 or age > 5000:warnings.append("QUOTE_AGE_OUTSIDE_DIAGNOSTIC_RANGE")
    if not (bid and ask and point and ask>=bid and side in ("BUY","SELL")):
        missing.append("VALID_SUBMISSION_QUOTE")
    else:
        sign=1 if side=="BUY" else -1
        entry=ask if sign==1 else bid
        exit_quote=bid if sign==1 else ask
        out["spread_points"]=(ask-bid)/point
        ref=number(market.get("ask" if sign==1 else "bid"),positive=True)
        rb,ra,rp=(number(market.get(k),positive=True) for k in ("bid","ask","point"))
        if ref and rb and ra and rp and ra>rb and isclose(point,rp,rel_tol=1e-9):
            deviation=sign*(entry-ref)/point
            out["adverse_entry_points"]=deviation
            out["adverse_entry_spread_ratio"]=deviation/((ra-rb)/point)
            if out["adverse_entry_spread_ratio"]>1+1e-9:warnings.append("ADVERSE_ENTRY_OVER_REFERENCE_SPREAD")
            if out["spread_points"]>(ra-rb)/point*1.5+1e-9:warnings.append("SPREAD_OVER_1_5_REFERENCE")
        else:missing.append("REFERENCE_QUOTE")
        if sl and tp:
            sd=sign*(exit_quote-sl)/point
            td=sign*(tp-exit_quote)/point
            out.update(stop_distance_points=sd,target_distance_points=td)
            if sd<=0 or td<=0 or sign*(tp-entry)<=0:warnings.append("INVALID_STOP_OR_TARGET_DIRECTION")
            if stops is None or stops<0:missing.append("BROKER_STOP_LEVEL")
            elif sd<stops-1e-7 or td<stops-1e-7:warnings.append("BELOW_BROKER_STOP_DISTANCE")
            if tick_size:
                if any(not isclose(v/tick_size,round(v/tick_size),rel_tol=0,abs_tol=1e-6) for v in (sl,tp)):
                    warnings.append("OFF_TICK_GRID")
            else:missing.append("TICK_SIZE")
        else:missing.append("STOP_OR_TARGET")
    if loss is None or loss>=0 or reward is None or reward<=0:
        missing.append("VALID_ESTIMATED_LOSS_OR_REWARD")
    else:
        out.update(estimated_loss_money=-loss,estimated_reward_money=reward,reward_risk=reward/-loss)
        if reserved is None:missing.append("RESERVED_RISK")
        elif -loss>reserved+0.000001:warnings.append("ESTIMATED_LOSS_ABOVE_RESERVED_RISK")
        if out["reward_risk"]<1.2-1e-9:warnings.append("REWARD_RISK_BELOW_DIAGNOSTIC_1_2")
    out["status"]="WARNING" if warnings else "INCOMPLETE" if missing else "CLEAR"
    return out
