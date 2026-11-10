"""Forward candidate journal; never writes orders, risk settings or learning labels.

The metric is one hypothetical hour of price movement after the next full M15
boundary, less the decision-time spread. It is not an SL/TP trade backtest or P&L.
"""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone as dt_timezone
import math

from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

from .models import CandidateObservation, EAHeartbeat, MarketBar

INTERVAL = timedelta(minutes=15)
VERSION = "candidate-1h-v1"


def record_candidates(decision, market, *, now=None):
    now = now or timezone.now()
    source = market.get("source", "")
    if not source:
        return 0
    signal = datetime.fromtimestamp(float(market["bar_time"]), dt_timezone.utc)
    # A forming candle has already begun when the request arrives. Do not use
    # that candle's earlier open as an achievable forward entry.
    start = datetime.fromtimestamp((int(now.timestamp())//900+1)*900, dt_timezone.utc)
    point, spread = float(market["point"]), float(market["ask"])-float(market["bid"])
    if not all(math.isfinite(v) for v in (point, spread)) or point <= 0 or spread < 0:
        return 0
    rows = []
    selection = decision.features.get("selection", {})
    for item in selection.get("candidates", []):
        setup = str(item.get("setup", "UNKNOWN"))[:64]
        side = item.get("side", "HOLD")
        disposition = ("SELECTED" if item.get("approved") and selection.get("selected_setup") == setup
                       and decision.action == side else "ELIGIBLE_ALTERNATIVE" if item.get("approved")
                       else "REJECTED")
        valid_direction = side in {"BUY", "SELL"}
        timely = signal.timestamp() % 900 == 0 and 0 <= (now-signal-INTERVAL).total_seconds() <= 120
        reason = "" if valid_direction and timely else "NO_DIRECTIONAL_SETUP" if not valid_direction else "LATE_OR_OFF_GRID_DECISION"
        rows.append(CandidateObservation(decision=decision, source=source, symbol=decision.symbol,
            signal_bar_time=signal, setup=setup, side=side if valid_direction else "HOLD", version=VERSION,
            disposition=disposition, recorded_at=now, evaluation_start=start, evaluation_end=start+4*INTERVAL,
            status="WAITING" if not reason else "SKIPPED", reason=reason,
            snapshot={"candidate": item, "context": selection.get("context", {}),
                      "decision_action": decision.action, "decision_reason": decision.rationale_code,
                      "model_version":decision.model_version,
                      "market_features":{k:decision.features.get(k) for k in ("ema20","ema50","atr","spread_points")},
                      "risk_policy":decision.features.get("risk_policy",{}),
                      "point": point, "assumed_spread": spread, "bid": market["bid"], "ask": market["ask"],
                      "metric": "NEXT_FULL_M15_OPEN_TO_ONE_HOUR_CLOSE_LESS_FROZEN_SPREAD",
                      "excluded_costs": ["SLIPPAGE", "COMMISSION", "SWAP"],
                      "policy": VERSION} ))
        row = rows[-1]
        row.plan_status = "UNAVAILABLE"
        row.plan_outcome = {"version":"sltp-6h-v1", "reason":"NO_VALID_DIRECTIONAL_PLAN"}
        try:
            stop, target = float(item["stop_price"]), float(item["target_price"])
            entry = float(market["ask"] if side == "BUY" else market["bid"])
            valid_plan = valid_direction and not reason and all(math.isfinite(v) and v>0 for v in (stop,target))
            valid_plan = valid_plan and (stop < entry < target if side == "BUY" else target < entry < stop)
            if valid_plan:
                row.plan_status = "WAITING"
                row.plan_end = start+24*INTERVAL
                row.plan_outcome = {"version":"sltp-6h-v1", "observed_bars":[], "stop":stop,"target":target,
                                    "assumptions":"FROZEN_SPREAD_STOP_FIRST_NO_SLIPPAGE_COMMISSION_SWAP"}
        except (KeyError,TypeError,ValueError,OverflowError):
            pass
    CandidateObservation.objects.bulk_create(rows, ignore_conflicts=True)
    return len(rows)


def settle_candidates(*, now=None):
    now = now or timezone.now()
    count = 0
    # Bound each poll; lock and recheck each row so concurrent workers settle once.
    due = list(CandidateObservation.objects.filter(status="WAITING", evaluation_end__lte=now)
               .order_by("evaluation_end").values_list("pk", flat=True)[:500])
    for pk in due:
        with transaction.atomic():
            row = CandidateObservation.objects.select_for_update().get(pk=pk)
            if row.status != "WAITING":
                continue
            candles = list(MarketBar.objects.filter(source=row.source, symbol=row.symbol, timeframe="M15",
                timestamp__gte=row.evaluation_start, timestamp__lt=row.evaluation_end,
                received_at__lte=now).order_by("timestamp"))
            expected = [row.evaluation_start+i*INTERVAL for i in range(4)]
            # Exact timestamp matching rejects off-grid duplicates and partial intervals.
            if [c.timestamp for c in candles] != expected:
                if now < row.evaluation_end+timedelta(hours=2):
                    continue
                row.status, row.reason = "SKIPPED", "MISSING_OR_OFF_GRID_OUTCOME_CANDLES"
            elif row.recorded_at >= row.evaluation_start:
                row.status, row.reason = "SKIPPED", "NOT_RECORDED_BEFORE_EVALUATION"
            elif any(not all(v.is_finite() and v > 0 for v in (c.open,c.high,c.low,c.close))
                     or not c.low <= min(c.open,c.close) <= max(c.open,c.close) <= c.high for c in candles):
                row.status, row.reason = "SKIPPED", "INVALID_OUTCOME_CANDLE"
            else:
                sign = 1 if row.side == "BUY" else -1
                point, spread = row.snapshot["point"], row.snapshot["assumed_spread"]
                opened, closed = float(candles[0].open), float(candles[-1].close)
                favorable = (max(float(c.high) for c in candles)-opened if sign > 0
                             else opened-min(float(c.low) for c in candles))
                adverse = (opened-min(float(c.low) for c in candles) if sign > 0
                           else max(float(c.high) for c in candles)-opened)
                row.outcome = {"entry_bid_open": opened, "exit_bid_close": closed,
                    "gross_directional_points": sign*(closed-opened)/point,
                    "spread_adjusted_points": (sign*(closed-opened)-spread)/point,
                    "favorable_bid_excursion_points": max(0,favorable/point),
                    "adverse_bid_excursion_points": max(0,adverse/point),
                    "candles": 4, "interpretation": "HYPOTHETICAL_PRICE_MOVEMENT_NOT_TRADE_PROFIT"}
                row.status, row.reason = "SCORED", "FORWARD_PRICE_WINDOW_COMPLETE"
            row.settled_at = now
            row.save(update_fields=["status","reason","outcome","settled_at"])
            count += 1
    return count


@require_GET
def candidate_status(request):
    feed = next((h for h in EAHeartbeat.objects.order_by("-last_seen")[:100]
                 if isinstance(h.payload,dict) and type(h.payload.get("account_trade_mode")) is int
                 and h.payload["account_trade_mode"] == 0), None)
    result = {"mode": "OBSERVATION_ONLY", "affects_orders": False, "groups": [], "recent": [],
              "metric": "One-hour hypothetical movement, less frozen spread; excludes slippage, commission and swap."}
    if feed is None:
        return JsonResponse({**result, "state": "NO_DEMO_FEED"})
    from .market_context import load_decision_context
    from ramon_pipeline.signalflow.trade_planning import market_context
    now=timezone.now()
    result["current_context"]=market_context({"bars":[{"time":now.timestamp()-900}],
        "decision_time":now.timestamp(),"context_bars":load_decision_context(
            source=feed.instance_id,symbol=feed.symbol,decision_time=now.timestamp())})
    rows = list(CandidateObservation.objects.filter(source=feed.instance_id, symbol=feed.symbol, version=VERSION)
                .order_by("-recorded_at","setup")[:3500])
    groups = defaultdict(list)
    for row in rows:
        groups[(row.setup,row.disposition)].append(row)
    for (setup, disposition), group in sorted(groups.items()):
        scored = [r.outcome["spread_adjusted_points"] for r in group if r.status == "SCORED"]
        result["groups"].append({"setup": setup, "disposition": disposition, "observations": len(group),
            "scored": len(scored), "positive": sum(v>0 for v in scored),
            "mean_points": sum(scored)/len(scored) if scored else None,
            "plan_counts":dict(Counter(r.plan_outcome.get("result",r.plan_status) for r in group)),
            "plan_mean_r":sum(r.plan_outcome["r"] for r in group if r.plan_status=="RESOLVED") /
                sum(r.plan_status=="RESOLVED" for r in group) if any(r.plan_status=="RESOLVED" for r in group) else None})
    result.update(state="COLLECTING" if rows else "WAITING_FOR_NEW_DECISION", source=feed.instance_id,
        symbol=feed.symbol, counts=dict(Counter(r.status for r in rows)), sample_limit=3500,
        recent=[{"setup": r.setup, "side": r.side, "disposition": r.disposition, "status":r.status,
                 "reason":r.reason, "recorded_at":r.recorded_at.isoformat(), "outcome":r.outcome,
                 "plan_status":r.plan_status,"plan_outcome":{k:v for k,v in r.plan_outcome.items() if k!="observed_bars"}} for r in rows[:14]])
    response = JsonResponse(result, json_dumps_params={"allow_nan":False})
    response["Cache-Control"] = "no-store"
    return response
