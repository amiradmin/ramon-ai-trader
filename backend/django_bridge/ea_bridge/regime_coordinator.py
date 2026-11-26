"""Advisory-only V12 coordinator. Never creates orders or changes controls."""
from datetime import timedelta
from django.conf import settings
from collections import Counter
import math
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from .models import DecisionEvaluation, StrategyWorkerControl, MarketBar, EAHeartbeat
from .strategy_worker import strategy_readiness
from .strategy_registry import STRATEGIES

TREND={"TREND_PULLBACK","BREAKOUT_RETEST"}
RANGE={"RANGE_EDGE_FADE"}
FLEX={"SESSION_OPENING_RANGE_BREAKOUT","VOLATILITY_COMPRESSION_EXPANSION",
      "EXHAUSTION_REVERSAL","FAILED_BREAKOUT_SWEEP"}

def suitability(setup, regime):
    if regime not in {"BULLISH","BEARISH","RANGING"}:
        return "UNCERTAIN"
    if setup in TREND:
        return "COMPATIBLE" if regime!="RANGING" else "MISMATCH"
    if setup in RANGE:
        return "COMPATIBLE" if regime=="RANGING" else "MISMATCH"
    return "SETUP_SPECIFIC_CHECK_REQUIRED"

def consensus(labels):
    labels=list(labels)
    return labels[0] if len(labels)>=2 and len(set(labels))==1 and labels[0] in {"BULLISH","BEARISH","RANGING"} else "UNCERTAIN"

def ema(values, n):
    value=values[0]
    for x in values[1:]:
        value+=(x-value)*2/(n+1)
    return value

def gap_details(stamps, minutes, closures=()):
    """Only explicitly verified UTC closures excuse missing complete bar slots."""
    step=timedelta(minutes=minutes)
    gaps=[]
    for a,b in zip(stamps,stamps[1:]):
        if b-a==step: continue
        count=max(0,int((b-a)/step)-1)
        verified=count>0 and any(start<=a+step and b<=end for start,end in closures)
        gaps.append(dict(previous=a.isoformat(),next=b.isoformat(),missing_slots=count,
                         classification="VERIFIED_CLOSURE" if verified else "UNVERIFIED_GAP"))
    return gaps

def verified_closures(source,symbol):
    # Optional, exact source/symbol-specific UTC windows confirmed by operator.
    entries=getattr(settings,"RAMON_COORDINATOR_VERIFIED_CLOSURES",[])
    windows=[]
    for item in entries if isinstance(entries,list) else []:
        if not isinstance(item,dict): continue
        if item.get("source")!=source or item.get("symbol")!=symbol: continue
        try:
            a,b=parse_datetime(item["start"]),parse_datetime(item["end"])
            if a and b and timezone.is_aware(a) and timezone.is_aware(b) and a<b:
                windows.append((a,b))
        except (KeyError,ValueError,TypeError): continue
    return windows

def alignment(context):
    a,b,c=(context[tf]["direction"] if context[tf]["status"]=="READY" else None for tf in ("M5","M15","H1"))
    if b not in ("BUY","SELL"): return "UNCERTAIN"
    if a not in ("BUY","SELL"): return "SHORT_TERM_UNAVAILABLE"
    if c in ("BUY","SELL") and c!=b: return "HIGHER_TIMEFRAME_CONFLICT"
    if a!=b: return "PULLBACK_OR_CONFLICT" if c==b else "SHORT_TERM_CONFLICT_H1_UNCONFIRMED"
    return "ALIGNED_ALL_TIMEFRAMES" if c==b else "M5_M15_ALIGNED_H1_UNCONFIRMED"

def timeframe_context(source,symbol,tf,minutes,now):
    end=now-timedelta(minutes=minutes)
    rows=list(MarketBar.objects.filter(source=source,symbol=symbol,timeframe=tf,
        timestamp__lte=end,received_at__lte=now).order_by("-timestamp")[:150])[::-1]
    if len(rows)<50:
        return {"status":"WARMUP","direction":None,"bars":len(rows)}
    last=rows[-1].timestamp
    age=(end-last).total_seconds()
    if not 0<=age<minutes*60:
        return {"status":"STALE","direction":None,"bars":len(rows)}
    recent=rows[-50:]
    gaps=gap_details([r.timestamp for r in recent],minutes,verified_closures(source,symbol))
    off_grid=[r.timestamp.isoformat() for r in recent if r.timestamp.timestamp()%(minutes*60)]
    diagnostics=dict(gaps=gaps,off_grid=off_grid,missing_slots=sum(g["missing_slots"] for g in gaps))
    if off_grid or any(g["classification"]!="VERIFIED_CLOSURE" for g in gaps):
        return {"status":"GAP_OR_OFF_GRID","direction":None,"bars":len(rows),**diagnostics}
    values=[float(r.close) for r in recent]
    if not all(math.isfinite(x) and x>0 for x in values):
        return {"status":"INVALID_CLOSE","direction":None}
    fast=ema(values,20); slow=ema(values,50)
    return {"status":"READY","direction":"BUY" if fast>slow else "SELL" if fast<slow else "FLAT",
            "ema20":fast,"ema50":slow,"last_closed":(last+timedelta(minutes=minutes)).isoformat(),"bars":50,**diagnostics}

def build_report(source,symbol,account_login,server_name,now=None):
    now=now or timezone.now()
    scope=dict(source=source,symbol=symbol,account_login=account_login,server_name=server_name)
    controls={c.setup:c for c in StrategyWorkerControl.objects.filter(**scope)}
    ids=[c.latest.get("evaluation_id") for c in controls.values() if type(c.latest.get("evaluation_id")) is int]
    evaluations={e.pk:e for e in DecisionEvaluation.objects.filter(pk__in=ids,instance_id=source,
        symbol=symbol,account_login=account_login,server_name=server_name).select_related("verdict")}
    feed=EAHeartbeat.objects.filter(instance_id=source,account_login=account_login,server_name=server_name,symbol=symbol).first()
    feed_state=strategy_readiness(feed,now=now)
    rows=[]; labels=[]; recorded=[]; candidates=[]
    for setup in STRATEGIES:
        c=controls.get(setup)
        row={"setup":setup,"status":"MISSING","eligible":False}
        rows.append(row)
        if not c: continue
        row.update(automatic=c.automatic,worker_state=c.state)
        try:
            stamp=parse_datetime(str(c.latest.get("evaluated_at","")))
            fresh=stamp is not None and 0<=(now-stamp).total_seconds()<=90 and c.last_seen is not None and 0<=(now-c.last_seen).total_seconds()<=90
        except (ValueError,TypeError): fresh=False
        e=evaluations.get(c.latest.get("evaluation_id"))
        if not c.automatic or not fresh or not e or e.features.get("strategy_setup")!=setup or not 0<=(now-e.created_at).total_seconds()<=90:
            row["status"]="PAUSED_OR_STALE_OR_UNVERIFIED"
            continue
        stages=e.features.get("stages") or {}
        opp=stages.get("opportunities") or {}
        regime=(opp.get("regime") or {}).get("label")
        bar=(e.features.get("market_snapshot") or {}).get("bar_time")
        row.update(status="OBSERVED",evaluation_id=e.pk,bar_time=bar,regime=regime,action=e.action,
            reason=e.rationale_code,risk_approved=e.verdict.approved,command_created=bool(c.latest.get("command_created")))
        if regime: recorded.append((bar,regime))
        expected_state=c.latest.get("command_reason") if c.latest.get("command_created") else c.latest.get("reason")
        current_bar=int(now.timestamp())//900*900-900
        if feed_state!="READY" or not expected_state or c.state!=expected_state or bar!=current_bar or c.state in {
            "TELEMETRY_STALE","QUOTE_STALE","TERMINAL_NOT_READY","M15_STALE","M15_WARMUP","EVALUATION_ERROR"}:
            row["status"]="RECORDED_NOT_CURRENT"
            continue
        row["status"]="CURRENT"
        if regime: labels.append((bar,regime))
        if e.action in ("BUY","SELL") and e.verdict.approved and all((stages.get(k) or {}).get("approved") is True for k in ("opportunities","entry_timing","risk_management")):
            score=opp.get("score")
            quality=(stages.get("entry_timing") or {}).get("quality")
            if all(type(x) in (int,float) and math.isfinite(x) and 0<=x<=1 for x in (score,quality)):
                row.update(eligible=True,score=score,quality=quality)
                candidates.append(row)
    # Never compare observations from different completed signal bars.
    bars={bar for bar,label in labels}
    regime=consensus(label for bar,label in labels) if len(bars)==1 and None not in bars else "UNCERTAIN"
    context={tf:timeframe_context(source,symbol,tf,m,now) for tf,m in (("M5",5),("M15",15),("H1",60))}
    for row in rows:
        row["regime_fit"]=suitability(row["setup"],regime)
        side=row.get("action")
        row["timeframe_alignment"]={tf:(v["direction"]==side if side in ("BUY","SELL") and v["status"]=="READY" else None) for tf,v in context.items()}
    ranked=sorted((r for r in candidates if len(bars)==1 and None not in bars and r["bar_time"] in bars),
        key=lambda r:(-(r["score"]+r["quality"])/2,r["setup"]))
    return {"version":"V12.3","mode":"ADVISORY_ONLY","live_promotion":False,"generated_at":now.isoformat(),
        "source":source,"symbol":symbol,"regime":regime,"regime_votes":dict(Counter(label for bar,label in labels)),
        "feed_readiness":feed_state,"recorded_regime_votes":dict(Counter(label for bar,label in recorded)),
        "timeframes":context,"alignment":alignment(context),"strategies":rows,"ranked_candidates":[dict(setup=r["setup"],action=r["action"],
            score=r["score"],quality=r["quality"],evaluation_id=r["evaluation_id"],regime_fit=r["regime_fit"]) for r in ranked],
        "limitations":["Current recorded evaluations only; not a synchronized execution arbiter.",
          "Equal-weight opportunity and entry-quality ranking is exploratory, not expected return.",
          "EMA direction is advisory; no live filter or shared portfolio allocation is applied.",
          "Fast Scalp uses a separate observation path and is not ranked here."]}
