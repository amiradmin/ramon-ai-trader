"""Offline only. Never alters risk verdicts or simulated position sizing."""
import math
from collections import Counter, defaultdict
from .missed_opportunities import audit_rejection

def finite(x):
    try:
        v=float(x)
        return v if math.isfinite(v) else None
    except (ValueError,TypeError,OverflowError):
        return None

def features(result, market):
    s=result["stages"]; op=s["opportunities"]; timing=s["entry_timing"]; risk=s["risk_management"]
    setup=op.get("setup") or {}
    atr=finite(setup.get("atr"))
    distance=finite(risk.get("stop_points"))
    return dict(score=finite(op.get("score")),quality=finite(timing.get("quality")),
        stop_atr=distance*market["point"]/atr if distance is not None and atr and atr>0 else None,
        regime=(op.get("regime") or {}).get("label"),
        side=op.get("side"),hour=((market["latest_bar_time"]+900)%86400)//3600)

def bucket(v, edges):
    if v is None:return "UNKNOWN"
    for i,e in enumerate(edges):
        if v<e:return f"<{e}" if i==0 else f"{edges[i-1]}–<{e}"
    return f">={edges[-1]}"

def dimensions(f):
    return dict(side=f.get("side") or "UNKNOWN",regime=f.get("regime") or "UNKNOWN",
        utc_block=str((f["hour"]//6)*6).zfill(2) if f.get("hour") is not None else "UNKNOWN",
        score=bucket(f.get("score"),(.55,.65,.75,.85)),
        quality=bucket(f.get("quality"),(.55,.65,.75,.85)),
        stop_atr=bucket(f.get("stop_atr"),(1,1.5,2,2.5,3)))

def rejected_volume(result,market,future,*,slippage_points,commission_per_lot):
    risk=result["stages"]["risk_management"]
    if risk.get("reason")!="RISK_BUDGET_BELOW_MINIMUM_VOLUME":return None
    record=audit_rejection(result,market,future,slippage_points=slippage_points)
    if record is None:return None
    record["features"]=features(result,market)
    if "gross_r" in record:
        distance=abs(record["hypothetical_entry"]-record["stop"])
        risk_per_lot=distance/market["point"]*market["point_value_per_lot"]
        if risk_per_lot>0:record["net_r"]=record["gross_r"]-commission_per_lot/risk_per_lot
    return record

def summarize_entries(trades,records):
    actual=defaultdict(list); rejected=defaultdict(list)
    for t in trades:
        for k,v in dimensions(t["entry_features"]).items():actual[k,v].append(t)
    for r in records:
        for k,v in dimensions(r["features"]).items():rejected[k,v].append(r)
    def counter(rows):
        resolved=[r["net_r"] for r in rows if "net_r" in r]
        return dict(observations=len(rows),outcomes=dict(Counter(r["outcome"] for r in rows)),
            resolved=len(resolved),positive=sum(x>0 for x in resolved),
            mean_net_r_resolved_only=sum(resolved)/len(resolved) if resolved else None)
    return dict(actual_buckets=[dict(dimension=k,bucket=v,trades=len(ts),
        wins=sum(t["pnl"]>0 for t in ts),net_pnl=round(sum(t["pnl"] for t in ts),2))
        for (k,v),ts in sorted(actual.items())],
        rejected_volume=counter(records),
        rejected_buckets=[dict(dimension=k,bucket=v,**counter(rs)) for (k,v),rs in sorted(rejected.items())],
        examples=records[:10],
        limitations="Overlapping rejected evaluations, not unique trades. Configured-R hypothetical ignores untested structural target checks. 32-bar horizon; gaps and unresolved excluded from mean. Fixed costs. No live approval.")
