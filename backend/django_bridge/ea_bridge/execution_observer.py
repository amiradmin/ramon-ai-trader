"""Observed broker costs, distinct from hypothetical candidate outcomes."""
import math
import json
from pathlib import Path

from django.http import JsonResponse
from django.views.decorators.http import require_GET

from .models import EACommand, EAHeartbeat, LearningTradeOutcome, StrategyDecisionRecord

NEW_METRICS={"entry_price","decision_quote_price","decision_to_fill_points","decision_spread_points",
             "request_price","request_fill_price","request_slippage_points","submission_spread_points","broker_commission","fee"}


def number(value, *, positive=False):
    try:
        if isinstance(value,bool):
            return None
        result=float(value)
        return result if math.isfinite(result) and (not positive or result>0) else None
    except (TypeError,ValueError,OverflowError):
        return None


def request_metrics(command, *, market=None):
    if command is None or command.action!="OPEN" or command.state!="EXECUTED":
        return {}
    raw=command.result.get("execution",{}) if isinstance(command.result,dict) else {}
    if not isinstance(raw,dict) or type(raw.get("version")) is not int or raw.get("version")!=1:
        return {}
    side=command.payload.get("side")
    if command.payload.get("engine") == "SCALPER":
        market = command.payload.get("market_snapshot", {})
    if command.evaluation_id:
        market = command.evaluation.features.get("market_snapshot", {})
    if market is None:
        decision=StrategyDecisionRecord.objects.filter(decision_id=command.decision_id,symbol=command.symbol).first()
        snapshot = command.evaluation if command.evaluation_id else decision
        market=snapshot.features.get("market_snapshot",{}) if decision else {}
    point=number(market.get("point"),positive=True)
    requested,filled=(number(raw.get(k),positive=True) for k in ("request_price","fill_price"))
    bid,ask=(number(raw.get(k),positive=True) for k in ("bid","ask"))
    metrics={}
    if requested is not None:
        metrics["request_price"]=requested
    if filled is not None:
        metrics["request_fill_price"]=filled
    if point and side in {"BUY","SELL"} and requested and filled:
        metrics["request_slippage_points"]=(1 if side=="BUY" else -1)*(filled-requested)/point
    if point and bid and ask and ask>=bid:
        metrics["submission_spread_points"]=(ask-bid)/point
    return metrics


def enrich_outcome(outcome, decision, command):
    metrics=request_metrics(command)
    if command is not None and (decision is not None or command.payload.get("engine") == "SCALPER"):
        snapshot = command.evaluation if command.evaluation_id else decision
        market = (command.payload.get("market_snapshot",{}) if command.payload.get("engine") == "SCALPER"
                  else snapshot.features.get("market_snapshot",{}))
        point=number(market.get("point"),positive=True)
        expected=number(market.get("ask" if command.payload.get("side")=="BUY" else "bid"),positive=True)
        fill=number(outcome.get("entry_price"),positive=True)
        bid,ask=(number(market.get(k),positive=True) for k in ("bid","ask"))
        if point and expected and fill and command.payload.get("side") in {"BUY","SELL"}:
            metrics.update(decision_quote_price=expected,
                decision_to_fill_points=(1 if command.payload.get("side")=="BUY" else -1)*(fill-expected)/point)
        if point and bid and ask and ask>=bid:
            metrics["decision_spread_points"]=(ask-bid)/point
    outcome.update(metrics)
    # Existing outcomes are immutable. Newly supported metadata must not turn an
    # exact retry of a legacy payload into a conflict or stall the EA cursor.
    existing=LearningTradeOutcome.objects.filter(trade_id=outcome["trade_id"]).first()
    if existing:
        for key in NEW_METRICS-set(existing.execution_metrics):
            outcome.pop(key,None)


@require_GET
def execution_status(request):
    feed=next((h for h in EAHeartbeat.objects.order_by("-last_seen")[:100]
        if isinstance(h.payload,dict) and type(h.payload.get("account_trade_mode")) is int
        and h.payload["account_trade_mode"]==0),None)
    result={"mode":"BROKER_OBSERVATIONS","affects_orders":False,"recent":[],"sample_limit":200}
    if feed is None:
        return JsonResponse({**result,"state":"NO_DEMO_FEED"})
    from .profit_protection import protection_status
    result["profit_protection"] = protection_status(feed)
    comparison_path = Path(__file__).with_name("research_results") / "exit_comparison_2026_11_09.json"
    if comparison_path.is_file():
        result["exit_comparison"] = json.loads(comparison_path.read_text())
    rows=list(LearningTradeOutcome.objects.filter(instance_id=feed.instance_id,account_login=feed.account_login,
        symbol=feed.symbol,closed_at__isnull=False).order_by("-closed_at")[:200])
    commands=list(EACommand.objects.select_related("evaluation").filter(instance_id=feed.instance_id,account_login=feed.account_login,
        symbol=feed.symbol,action="OPEN",state="EXECUTED").order_by("-created_at")[:200])
    # Request measurements can be available before a position closes.
    markets={(key,symbol):snapshot or {} for key,symbol,snapshot in StrategyDecisionRecord.objects.filter(
        decision_id__in=[c.decision_id for c in commands]).values_list("decision_id","symbol","features__market_snapshot")}
    requests=[request_metrics(c,market=markets.get((c.decision_id,c.symbol),{})) for c in commands]
    metrics={}
    for key in ("commission","swap","broker_commission","fee","decision_spread_points","decision_to_fill_points"):
        values=[number(r.execution_metrics.get(key)) for r in rows]
        values=[v for v in values if v is not None]
        metrics[key]={"observations":len(values),"unavailable":len(rows)-len(values),
                      "average":sum(values)/len(values) if values else None}
    for key in ("request_slippage_points","submission_spread_points"):
        values=[m[key] for m in requests if key in m]
        metrics[key]={"observations":len(values),"unavailable":len(commands)-len(values),
                      "average":sum(values)/len(values) if values else None}
    result.update(state="COLLECTING" if rows or commands else "WAITING_FOR_BROKER_DATA",metrics=metrics,
        source=feed.instance_id,account_login=feed.account_login,symbol=feed.symbol,
        ea_version=feed.ea_version,
        closed_trades=len(rows),executed_open_commands=len(commands),
        recent_requests=[{"command_id":c.command_id,"reported_at":c.completed_at.isoformat() if c.completed_at else None,
                          "metrics":m} for c,m in zip(commands,requests) if m][:10],
        precision="Request slippage is signed: positive adverse, negative favorable. Decision-to-fill also includes delay.",
        recent=[{"trade_id":r.trade_id,"closed_at":r.closed_at.isoformat(),"net_pnl":float(r.realized_pnl),
                 "metrics":r.execution_metrics} for r in rows[:10]])
    response=JsonResponse(result,json_dumps_params={"allow_nan":False})
    response["Cache-Control"]="no-store"
    return response
