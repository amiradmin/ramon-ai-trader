"""Read-only recorded-outcome analytics, not a complete broker statement."""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from django.conf import settings
from django.utils import timezone
from .account_scope import real_cent_scope
from .models import EAHeartbeat, EACommand, LearningTradeOutcome
from .strategy_registry import STRATEGIES

def attribution(outcome, command, server):
    if not command:
        return "UNATTRIBUTED", "COMMAND_LINK_MISSING"
    tags=outcome.tags or {}
    payload=command.payload or {}
    if not (command.action=="OPEN" and command.state=="EXECUTED" and command.execution_authorized
            and command.instance_id==outcome.instance_id and command.account_login==outcome.account_login
            and command.symbol==outcome.symbol and payload.get("server_name")==server
            and tags.get("command_id")==command.command_id and command.decision_id==outcome.decision_id):
        return "UNATTRIBUTED", "COMMAND_SCOPE_OR_AUTHORIZATION_MISMATCH"
    ticket=str((command.result or {}).get("broker_ticket") or "")
    if not ticket or str(tags.get("broker_ticket") or "")!=ticket:
        return "UNATTRIBUTED", "BROKER_TICKET_UNVERIFIED"
    if command.completed_at and command.completed_at>outcome.closed_at:
        return "UNATTRIBUTED", "OUTCOME_PRECEDES_COMMAND_ACK"
    engine=payload.get("engine")
    setup=payload.get("strategy_setup")
    if engine=="STRATEGY" and setup in STRATEGIES and payload.get("magic")==STRATEGIES[setup][2]:
        return setup,"VERIFIED_COMMAND"
    if engine=="SCALPER" and payload.get("magic")==26092213:
        return "FAST_SCALP","VERIFIED_COMMAND"
    return "UNATTRIBUTED","ENGINE_OR_MAGIC_UNVERIFIED"

def number(value):
    try:
        if isinstance(value,bool): return None
        d=Decimal(str(value))
        return d if d.is_finite() else None
    except (InvalidOperation,TypeError,ValueError):
        return None

def metrics(rows):
    pnl=[Decimal(str(r["net_pnl"])) for r in rows]
    gains=sum((max(p,0) for p in pnl),Decimal(0))
    losses=sum((max(-p,0) for p in pnl),Decimal(0))
    total=gains-losses
    cumulative=peak=drawdown=Decimal(0)
    streak=longest=0
    # Same timestamp: aggregate to avoid arbitrary deal ordering drawdown.
    by_time=defaultdict(Decimal)
    for r,p in zip(rows,pnl): by_time[r["closed_at"]]+=p
    for stamp in sorted(by_time):
        p=by_time[stamp]; cumulative+=p; peak=max(peak,cumulative)
        drawdown=max(drawdown,peak-cumulative)
        streak=streak+1 if p<0 else 0; longest=max(longest,streak)
    costs={}
    for key in ("commission","broker_commission","swap","fee"):
        values=[number(r["execution_metrics"].get(key)) for r in rows]
        known=[v for v in values if v is not None]
        costs[key]={"reported_sum":float(sum(known)) if known else None,"coverage":len(known)}
    return dict(outcomes=len(rows),wins=sum(p>0 for p in pnl),losses=sum(p<0 for p in pnl),
        breakeven=sum(p==0 for p in pnl),win_rate=round(100*sum(p>0 for p in pnl)/len(pnl),2) if pnl else None,
        net_pnl=float(total),gross_winning_pnl=float(gains),gross_losing_pnl=float(losses),
        profit_factor=round(float(gains/losses),4) if losses else None,
        expectancy=float(total/len(pnl)) if pnl else None,closed_pnl_drawdown=float(drawdown),
        longest_losing_timestamp_streak=longest,costs=costs)

def build_report(days=30,now=None):
    if type(days) is not int or not 1<=days<=365: raise ValueError("days must be 1..365")
    now=now or timezone.now()
    source=getattr(settings,"RAMON_REAL_CENT_INSTANCE","")
    feed=EAHeartbeat.objects.filter(instance_id=source).first()
    ok,reason=real_cent_scope(feed)
    result=dict(version="V14.1",mode="READ_ONLY_RECORDED_OUTCOMES",affects_orders=False,
        generated_at=now.isoformat(),status=reason if not ok else "READY",days=days)
    if not ok: return result
    start=now-timedelta(days=days)
    qs=LearningTradeOutcome.objects.filter(instance_id=feed.instance_id,account_login=feed.account_login,
        symbol=feed.symbol,closed_at__gte=start,closed_at__lte=now).order_by("closed_at","pk")
    outcomes=list(qs[:10001])
    if len(outcomes)>10000:
        return dict(result,status="OUTCOME_LIMIT_EXCEEDED")
    ids={o.tags.get("command_id") for o in outcomes if isinstance(o.tags,dict) and isinstance(o.tags.get("command_id"),str)}
    commands={c.command_id:c for c in EACommand.objects.filter(command_id__in=ids,instance_id=feed.instance_id,
        account_login=feed.account_login,symbol=feed.symbol,action="OPEN",payload__server_name=feed.server_name)}
    rows=[]
    for o in outcomes:
        tags=o.tags if isinstance(o.tags,dict) else {}
        command=commands.get(tags.get("command_id"))
        strategy,why=attribution(o,command,feed.server_name)
        rows.append(dict(trade_id=o.trade_id,closed_at=o.closed_at.isoformat(),opened_at=o.opened_at.isoformat() if o.opened_at else None,
            strategy=strategy,attribution=why,net_pnl=str(o.realized_pnl),
            command_id=command.command_id if strategy!="UNATTRIBUTED" else None,
            broker_ticket=str(tags.get("broker_ticket") or "") if strategy!="UNATTRIBUTED" else None,
            execution_metrics=o.execution_metrics if isinstance(o.execution_metrics,dict) else {}))
    verified=[r for r in rows if r["strategy"]!="UNATTRIBUTED"]
    unknown=[r for r in rows if r["strategy"]=="UNATTRIBUTED"]
    return dict(result,source=feed.instance_id,symbol=feed.symbol,server=feed.server_name,
        currency=feed.payload["account"]["currency"],start=start.isoformat(),end=now.isoformat(),
        heartbeat_age_seconds=(now-feed.last_seen).total_seconds(),
        verified=metrics(verified),unattributed=metrics(unknown),
        strategies=[dict(strategy=s,**metrics([r for r in verified if r["strategy"]==s])) for s in [*STRATEGIES,"FAST_SCALP"]],
        outcomes=rows,limitations=["Only ingested outcomes; completeness against MT5 history not verified.",
            "Unattributed outcomes may lack historical server identity and are excluded from verified totals.",
            "Outcome counts are not necessarily unique fully closed positions; partial exits may be separate records.",
            "Realized P&L is stored net as reported; fees are coverage diagnostics and are not subtracted again.",
            "Closed P&L drawdown in account units, not floating equity or account percentage. Deposits excluded.",
            "No account currency conversion or cent-to-dollar assumption."])
