"""Forward, bounded SL/TP simulation on frozen observed bid candles."""
from datetime import timedelta
import math

from django.db import transaction
from django.utils import timezone

from .models import CandidateObservation, MarketBar

INTERVAL=timedelta(minutes=15)


def evaluate_plan(bars, *, side, stop, target, spread, point):
    """Use next full bar open; short exits buy at frozen-spread ask prices."""
    sign=1 if side=="BUY" else -1
    entry=bars[0]["open"]+(spread if sign>0 else 0)
    distance=sign*(entry-stop)
    if distance<=0 or sign*(target-entry)<=0:
        return {"result":"INVALID_AT_ENTRY", "entry":entry}
    for i,bar in enumerate(bars):
        adjustment=spread if sign<0 else 0
        opened,high,low=(bar[k]+adjustment for k in ("open","high","low"))
        stop_hit=low<=stop if sign>0 else high>=stop
        target_hit=high>=target if sign>0 else low<=target
        if sign*(opened-stop)<=0:
            price,result=opened,"STOP_GAP"
        elif sign*(opened-target)>=0:
            price,result=target,"TARGET"
        elif stop_hit:
            price,result=stop,"STOP"
        elif target_hit:
            price,result=target,"TARGET"
        elif i==23:
            price,result=bar["close"]+adjustment,"TIME_EXIT"
        else:
            continue
        move=sign*(price-entry)
        return {"result":result,"entry":entry,"exit":price,"bars_held":i+1,
                "exit_candle_time":bar["time"],"points":move/point,"r":move/distance,
                "ambiguous_stop_target":bool(stop_hit and target_hit and result=="STOP"),
                "interpretation":"HYPOTHETICAL_NOT_BROKER_PNL"}
    return {"result":"OPEN","entry":entry}


def settle_plans(*,now=None):
    now=now or timezone.now()
    ids=list(CandidateObservation.objects.filter(plan_status__in=["WAITING","OPEN"],
             evaluation_start__lte=now-INTERVAL).order_by("evaluation_start").values_list("pk",flat=True)[:250])
    settled=0
    for pk in ids:
        with transaction.atomic():
            row=CandidateObservation.objects.select_for_update().get(pk=pk)
            if row.plan_status not in {"WAITING","OPEN"}:
                continue
            state=dict(row.plan_outcome)
            bars=list(state.get("observed_bars",[]))
            if row.recorded_at>=row.evaluation_start or row.plan_end!=row.evaluation_start+24*INTERVAL:
                row.plan_status="SKIPPED"
                state["result"]="INVALID_FORWARD_WINDOW"
            else:
                while len(bars)<24:
                    expected=row.evaluation_start+len(bars)*INTERVAL
                    if expected+INTERVAL>now:
                        break
                    # Include the whole expected slot to detect shifted duplicates.
                    available=list(MarketBar.objects.filter(source=row.source,symbol=row.symbol,timeframe="M15",
                        timestamp__gte=expected,timestamp__lt=expected+INTERVAL,received_at__lte=now).order_by("timestamp"))
                    if len(available)!=1 or available[0].timestamp!=expected:
                        if now>=expected+INTERVAL+timedelta(hours=2):
                            row.plan_status="SKIPPED";state["result"]="DATA_GAP"
                        break
                    bar=available[0]
                    values={k:float(getattr(bar,k)) for k in ("open","high","low","close")}
                    if (not all(math.isfinite(v) and v>0 for v in values.values()) or
                        not values["low"]<=min(values["open"],values["close"])<=max(values["open"],values["close"])<=values["high"]):
                        row.plan_status="SKIPPED";state["result"]="INVALID_CANDLE";break
                    bars.append({"time":expected.isoformat(),**values})
                    result=evaluate_plan(bars,side=row.side,stop=state["stop"],target=state["target"],
                        spread=row.snapshot["assumed_spread"],point=row.snapshot["point"])
                    state.update(result)
                    if result["result"]!="OPEN":
                        row.plan_status="RESOLVED" if result["result"]!="INVALID_AT_ENTRY" else "SKIPPED"
                        break
                    row.plan_status="OPEN"
            state["observed_bars"]=bars
            row.plan_outcome=state
            if row.plan_status in {"RESOLVED","SKIPPED"}:
                row.plan_settled_at=now;settled+=1
            row.save(update_fields=["plan_status","plan_outcome","plan_settled_at"])
    return settled
