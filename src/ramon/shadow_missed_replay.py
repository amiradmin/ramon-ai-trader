"""Conservative, shadow-only M1 replay of independent WAIT candidates.

Uses ONLY observed decision snapshots and subsequent completed bars.
Never estimates fillability or treats a simulated win as real missed profit.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

def select_candidates(decisions, limit=120):
    """One earliest eligible WAIT per signal bar and side; no hindsight ranking."""
    chosen={}
    for d in sorted(decisions,key=lambda x:(x["captured"],x["sample_key"])):
        if str(d.get("final_decision") or "").upper()!="WAIT":continue
        meta=d.get("model_metadata") or {}
        if not isinstance(meta,dict):continue
        audit=meta.get("decision_audit") or {}
        base=audit.get("base") or {}
        final=audit.get("final") or {}
        if not isinstance(base,dict) or not isinstance(final,dict):continue
        side=str(final.get("candidate_direction") or base.get("decision") or "").upper()
        if side not in ("BUY","SELL"):continue
        try:
            bar=int(d["signal_bar_time"])
            spread=float(d.get("spread"))
            stop=float(base.get("stop_distance"))
            target=float(base.get("target_distance"))
            if not 0<spread<50 or not 0<stop<500 or not 0<target<500:continue
        except (TypeError,ValueError,OverflowError):continue
        k=(bar,side)
        if k not in chosen:
            chosen[k]={"signal_bar_time":bar,"direction":side,"captured":int(d["captured"]),
                       "sample_key":d["sample_key"],"spread":spread,
                       "stop_distance":stop,"target_distance":target,
                       "reason":str(final.get("reason") or "UNKNOWN")}
    return list(chosen.values())[:limit]


def replay_one(candidate, bars, horizon=30):
    """Simulated next-M1-open fill. Same-bar TP/SL tie is AMBIGUOUS, not a win."""
    cutoff=int(candidate["captured"])
    next_bars=[b for b in bars if int(b["time"])>cutoff]
    if not next_bars:return {"status":"INSUFFICIENT_M1"}
    first=next_bars[0]
    # Do not use an old M1 bar as if it were the immediate executable quote.
    if first["time"]-cutoff>120:return {"status":"M1_GAP"}
    spread=float(candidate["spread"])
    side=candidate["direction"]
    # Bar OHLC represents reference midpoint; half-spread on entry and exit.
    entry=float(first["open"])+(spread/2 if side=="BUY" else -spread/2)
    stop=float(candidate["stop_distance"]);target=float(candidate["target_distance"])
    sl=entry-stop if side=="BUY" else entry+stop
    tp=entry+target if side=="BUY" else entry-target
    observed=0
    for bar in next_bars[:horizon]:
        if observed and int(bar["time"])-int(prev)>120:
            return {"status":"M1_GAP","entry":round(entry,5),"bars_observed":observed}
        prev=bar["time"];observed+=1
        high=float(bar["high"]);low=float(bar["low"])
        if side=="BUY":
            hit_sl=low-spread/2<=sl
            hit_tp=high-spread/2>=tp
        else:
            hit_sl=high+spread/2>=sl
            hit_tp=low+spread/2<=tp
        common={"entry":round(entry,5),"stop":round(sl,5),"target":round(tp,5),
                "bars_observed":observed}
        if hit_sl and hit_tp:return dict(common,status="AMBIGUOUS_SAME_BAR")
        if hit_sl:return dict(common,status="SIMULATED_SL")
        if hit_tp:return dict(common,status="SIMULATED_TP")
    return {"status":"UNRESOLVED","entry":round(entry,5),"bars_observed":observed}


def replay_report(decisions, bars, limit=120):
    candidates=select_candidates(decisions,limit)
    outcomes=[]
    for c in candidates:
        finding=replay_one(c,bars)
        outcomes.append(dict(c,**finding))
    counts={}
    for o in outcomes:counts[o["status"]]=counts.get(o["status"],0)+1
    return {"status":"SHADOW_M1_REPLAY_NOT_EXECUTABLE",
            "independent_candidate_count":len(outcomes),
            "outcome_counts":counts,
            "cases":outcomes[:80],
            "assumptions":["One earliest WAIT per recorded signal bar and candidate direction.",
                "Entry simulated at first following M1 open; fixed observed decision spread.",
                "Both TP and SL inside one M1 bar is ambiguous, never a winner.",
                "No broker fill, slippage, order permissions, commission, or live spread proof.",
                "A simulated TP is NOT a verified profitable missed trade."]}
