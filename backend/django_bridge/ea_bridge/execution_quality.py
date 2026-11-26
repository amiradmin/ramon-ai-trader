"""Server-clock latency and observed entry deviations; never authorizes orders."""
from collections import Counter
from datetime import timedelta
from statistics import median
from django.conf import settings
from django.utils import timezone
from .account_scope import real_cent_scope
from .models import EACommand, EAHeartbeat, ScalpObservation
from .strategy_registry import STRATEGIES
from .execution_observer import number

THRESHOLDS=(0.5,1,2,3)

def seconds(a,b):
    if a is None or b is None: return None
    value=(b-a).total_seconds()
    return value if value>=0 else None

def price_metrics(side, market, request):
    point=number(market.get("point"),positive=True)
    bid=number(market.get("bid"),positive=True)
    ask=number(market.get("ask"),positive=True)
    requested=number(request.get("request_price"),positive=True)
    filled=number(request.get("request_fill_price"),positive=True)
    spread=(ask-bid)/point if point and bid and ask and ask>=bid else None
    expected=ask if side=="BUY" else bid if side=="SELL" else None
    sign=1 if side=="BUY" else -1
    before=sign*(requested-expected)/point if point and expected and requested else None
    total=sign*(filled-expected)/point if point and expected and filled else None
    broker=sign*(filled-requested)/point if point and expected and filled and requested else None
    ratio=before/spread if before is not None and spread and spread>0 else None
    return dict(decision_spread_points=spread,decision_to_request_points=before,
        decision_to_fill_points=total,request_slippage_points=broker,
        adverse_request_spread_ratio=ratio,
        hypothetical_warning_thresholds=[x for x in THRESHOLDS if ratio is not None and ratio>x and abs(ratio-x)>1e-9])

def summary(rows,key):
    values=sorted(r[key] for r in rows if r.get(key) is not None)
    n=len(values)
    return dict(available=n,missing=len(rows)-n,mean=sum(values)/n if n else None,
        median=median(values) if n else None,p95=values[(95*n+99)//100-1] if n else None,
        minimum=values[0] if n else None,maximum=values[-1] if n else None)

def strategy_name(c):
    p=c.payload
    setup=p.get("strategy_setup")
    if p.get("engine")=="STRATEGY" and setup in STRATEGIES and p.get("magic")==STRATEGIES[setup][2]:
        return setup
    if p.get("engine")=="SCALPER" and p.get("magic")==26092213:
        return "FAST_SCALP"
    return "UNVERIFIED_ENGINE"

def build_report(days=30,now=None):
    if type(days) is not int or not 1<=days<=365: raise ValueError("days must be 1..365")
    now=now or timezone.now()
    feed=EAHeartbeat.objects.filter(instance_id=getattr(settings,"RAMON_REAL_CENT_INSTANCE","")).first()
    ok,reason=real_cent_scope(feed)
    result=dict(version="V14.2",mode="OBSERVATION_ONLY",affects_orders=False,
        generated_at=now.isoformat(),days=days,status="READY" if ok else reason)
    if not ok:return result
    commands=list(EACommand.objects.filter(instance_id=feed.instance_id,account_login=feed.account_login,
        symbol=feed.symbol,payload__server_name=feed.server_name,action="OPEN",execution_authorized=True,
        created_at__gte=now-timedelta(days=days),created_at__lte=now).select_related("evaluation").order_by("created_at","pk")[:10001])
    if len(commands)>10000:return dict(result,status="COMMAND_LIMIT_EXCEEDED")
    ids={c.payload.get("scalp_observation_id") for c in commands if type(c.payload.get("scalp_observation_id")) is int}
    observations={o.pk:o for o in ScalpObservation.objects.filter(pk__in=ids,source=feed.instance_id,
        account_login=feed.account_login,server_name=feed.server_name,symbol=feed.symbol)}
    rows=[]
    for c in commands:
        name=strategy_name(c)
        if name=="UNVERIFIED_ENGINE":continue
        market={}; stamp=None; basis=None
        if name=="FAST_SCALP":
            market=c.payload.get("market_snapshot") or {}
            obs=observations.get(c.payload.get("scalp_observation_id"))
            if obs and obs.action==c.payload.get("side"):
                stamp=obs.recorded_at;basis="SCALP_OBSERVATION_RECORDED"
        elif c.evaluation_id:
            e=c.evaluation
            if (e.instance_id,e.account_login,e.server_name,e.symbol,e.decision_id,e.action)==(
                c.instance_id,c.account_login,feed.server_name,c.symbol,c.decision_id,c.payload.get("side")):
                market=e.features.get("market_snapshot") or {}
                stamp=e.created_at;basis="EVALUATION_PERSISTED"
        if not isinstance(market,dict):market={}
        # Only use the scope-verified market. No legacy decision lookup.
        raw=c.result.get("execution",{}) if isinstance(c.result,dict) else {}
        valid=c.state=="EXECUTED" and isinstance(raw,dict) and type(raw.get("version")) is int and raw["version"]==1
        request={"request_price":raw.get("request_price"),"request_fill_price":raw.get("fill_price")} if valid else {}
        row=dict(command_id=c.command_id,strategy=name,state=c.state,side=c.payload.get("side"),
            created_at=c.created_at.isoformat(),claimed_at=c.claimed_at.isoformat() if c.claimed_at else None,
            completed_at=c.completed_at.isoformat() if c.completed_at else None,decision_timestamp_basis=basis,
            decision_to_command_seconds=seconds(stamp,c.created_at),
            command_to_claim_seconds=seconds(c.created_at,c.claimed_at),
            claim_to_terminal_seconds=seconds(c.claimed_at,c.completed_at),
            command_to_terminal_seconds=seconds(c.created_at,c.completed_at),
            **price_metrics(c.payload.get("side"),market,request))
        rows.append(row)
    keys=("decision_to_command_seconds","command_to_claim_seconds","claim_to_terminal_seconds",
          "command_to_terminal_seconds","decision_spread_points","decision_to_request_points",
          "decision_to_fill_points","request_slippage_points","adverse_request_spread_ratio")
    def aggregate(group):
        fills=[r for r in group if r["state"]=="EXECUTED"]
        return dict(commands=len(group),states=dict(Counter(r["state"] for r in group)),
            latency={k:summary(group,k) for k in keys[:4]},prices={k:summary(fills,k) for k in keys[4:]},
            warning_counts={str(x):sum(x in r["hypothetical_warning_thresholds"] for r in fills) for x in THRESHOLDS})
    return dict(result,source=feed.instance_id,server=feed.server_name,symbol=feed.symbol,
        heartbeat_age_seconds=(now-feed.last_seen).total_seconds(),excluded_unverified_commands=len(commands)-len(rows),
        totals=aggregate(rows),strategies=[dict(strategy=s,**aggregate([r for r in rows if r["strategy"]==s])) for s in [*STRATEGIES,"FAST_SCALP"]],
        commands=rows,thresholds=list(THRESHOLDS),
        limitations=["Server timestamp intervals are not network or broker-only latency.",
            "Evaluation persistence and scalp observation recording are different timing anchors.",
            "Scalper price reference is its command market snapshot, not necessarily the observation quote.",
            "Request-to-fill uses execution acknowledgement prices; no closed-outcome duplication.",
            "Warning thresholds are retrospective, strictly greater than multiples of reference spread, and do not block orders.",
            "Missing and inverted timestamps remain unavailable. Quote freshness at execution and EA receive/submit timestamps are not established by this report."])
