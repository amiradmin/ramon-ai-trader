"""V14.3.1: diagnostic metadata, never a trading command or authorization."""
import json
import logging
import uuid
from datetime import timedelta
from django.conf import settings
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET
from django.views.decorators.cache import never_cache
from django.contrib.admin.views.decorators import staff_member_required
from .account_scope import real_cent_scope
from .auth import BearerTokenRequired
from .commands import next_command, _body
from .models import EAHeartbeat, CommunicationProbeRun
from .execution_quality import summary
from .execution_observer import number

log=logging.getLogger(__name__)
COUNTS=(10,20,50)
SPACING=1.37
TTL=10

def stamp(value):
    return parse_datetime(value)

def feed_for_scope():
    feed=EAHeartbeat.objects.filter(instance_id=getattr(settings,"RAMON_REAL_CENT_INSTANCE","")).first()
    ok,_=real_cent_scope(feed)
    return feed if ok else None

def same_scope(run,feed):
    return (run.instance_id,run.account_login,run.server_name,run.symbol)==(
        feed.instance_id,feed.account_login,feed.server_name,feed.symbol)

def new_samples(count,now):
    return [dict(id=uuid.uuid4().hex,due_at=(now+timedelta(seconds=1+i*SPACING)).isoformat(),
        claimed_at=None,echoed_at=None,poll_http_ms=None) for i in range(count)]

def advance(samples,now,echo_id,echo_http):
    """Mutate only diagnostic state. One outstanding sample at a time."""
    accepted=False
    for s in samples:
        if s["id"]==echo_id and s["claimed_at"]:
            if s["echoed_at"]:
                accepted=True
            elif now<=stamp(s["due_at"])+timedelta(seconds=TTL):
                s["echoed_at"]=now.isoformat()
                value=number(echo_http)
                s["poll_http_ms"]=value if value is not None and 0<=value<=60000 else None
                accepted=True
            break
    if any(s["claimed_at"] and not s["echoed_at"] and
           now<=stamp(s["due_at"])+timedelta(seconds=TTL) for s in samples):
        return accepted,None
    for s in samples:
        due=stamp(s["due_at"])
        if not s["claimed_at"] and due<=now<=due+timedelta(seconds=TTL):
            s["claimed_at"]=now.isoformat()
            return accepted,s["id"]
    return accepted,None

def report(run,now):
    rows=[]
    for s in run.samples:
        due=stamp(s["due_at"])
        claimed=stamp(s["claimed_at"]) if s["claimed_at"] else None
        echoed=stamp(s["echoed_at"]) if s["echoed_at"] else None
        state="ECHOED" if echoed else "TIMED_OUT" if now>due+timedelta(seconds=TTL) else "CLAIMED" if claimed else "PENDING"
        rows.append(dict(s,state=state,claim_delay_ms=(claimed-due).total_seconds()*1000 if claimed else None,
                         claim_to_echo_ms=(echoed-claimed).total_seconds()*1000 if echoed and claimed else None))
    counts={k:sum(r["state"]==k for r in rows) for k in ("ECHOED","TIMED_OUT","CLAIMED","PENDING")}
    complete=not(counts["CLAIMED"] or counts["PENDING"])
    return dict(version="V14.3.1",mode="NON_TRADING_DIAGNOSTIC",affects_orders=False,
        run_id=run.run_id,status="COMPLETE" if complete else "RUNNING",
        started_at=run.started_at.isoformat(),deadline=run.deadline.isoformat(),counts=counts,
        metrics={k:summary(rows,k) for k in ("claim_delay_ms","poll_http_ms","claim_to_echo_ms")},
        samples=rows,limitations=["Only idle command polls carry probes; real commands always take priority.",
            "Claim delay includes polling schedule and server processing, not broker execution.",
            "Echo arrives on a later idle poll; it is not a network round-trip measurement.",
            "Timed out probes may reflect busy polling, missing EA support or connectivity.",
            "Only the latest run is retained; download before starting another."])

@staff_member_required
@require_POST
@never_cache
def start(request):
    try:
        count=_body(request).get("count")
    except (ValueError,UnicodeDecodeError):
        return JsonResponse({"error":"INVALID_BODY"},status=400)
    if type(count) is not int or count not in COUNTS:
        return JsonResponse({"error":"COUNT_MUST_BE_10_20_OR_50"},status=400)
    feed=feed_for_scope()
    now=timezone.now()
    if not feed or feed.ea_version not in ("1.11","1.12") or not feed.terminal_connected or not 0<=(now-feed.last_seen).total_seconds()<=30:
        return JsonResponse({"error":"FRESH_PROBE_CAPABLE_EA_REQUIRED"},status=409)
    with transaction.atomic():
        # Lock the existing unique heartbeat to serialize simultaneous starts.
        feed=EAHeartbeat.objects.select_for_update().get(pk=feed.pk)
        ok,_=real_cent_scope(feed)
        now=timezone.now()
        if not ok or feed.ea_version not in ("1.11","1.12") or not feed.terminal_connected or not 0<=(now-feed.last_seen).total_seconds()<=30:
            return JsonResponse({"error":"FRESH_PROBE_CAPABLE_EA_REQUIRED"},status=409)
        existing=CommunicationProbeRun.objects.select_for_update().filter(instance_id=feed.instance_id).first()
        if existing and report(existing,now)["status"]=="RUNNING":
            return JsonResponse({"error":"RUN_ALREADY_ACTIVE"},status=409)
        samples=new_samples(count,now)
        run,_=CommunicationProbeRun.objects.update_or_create(instance_id=feed.instance_id,defaults=dict(
            account_login=feed.account_login,server_name=feed.server_name,symbol=feed.symbol,
            run_id=uuid.uuid4().hex,started_at=now,deadline=stamp(samples[-1]["due_at"])+timedelta(seconds=TTL),samples=samples))
    return JsonResponse(report(run,now),status=201)

@staff_member_required
@require_GET
@never_cache
def status(request):
    feed=feed_for_scope()
    run=CommunicationProbeRun.objects.filter(instance_id=feed.instance_id).first() if feed else None
    data=report(run,timezone.now()) if run and same_scope(run,feed) else dict(version="V14.3.1",status="NO_RUN",affects_orders=False)
    response=JsonResponse(data,json_dumps_params={"allow_nan":False})
    if request.GET.get("download")=="1":
        response["Content-Disposition"]='attachment; filename="communication-v14.3.1.json"'
    return response

@csrf_exempt
@require_POST
def next_with_probe(request):
    # Original endpoint remains the sole trading authority. No diagnostic
    # database access before processing an actual order.
    response=next_command(request)
    if response.status_code!=200:
        return response
    try:
        p=_body(request)
        if p.get("probe_protocol")!="ramon-probe-v1":
            return response
        data=json.loads(response.content)
        if data.get("command") is not None or data.get("ok") is not True:
            return response
        if BearerTokenRequired.reject_if_invalid(request) is not None:
            return response
        feed=feed_for_scope()
        if not feed or feed.ea_version not in ("1.11","1.12") or (p.get("instance_id"),p.get("account_login"),p.get("server_name"),p.get("symbol"))!=(
            feed.instance_id,feed.account_login,feed.server_name,feed.symbol):
            return response
        now=timezone.now()
        if not feed.terminal_connected or not 0<=(now-feed.last_seen).total_seconds()<=30:
            return response
        with transaction.atomic():
            run=CommunicationProbeRun.objects.select_for_update().filter(instance_id=feed.instance_id,deadline__gte=now).first()
            if not run or not same_scope(run,feed):
                return response
            accepted,probe_id=advance(run.samples,now,p.get("probe_echo"),p.get("probe_http_ms"))
            if accepted or probe_id:
                run.save(update_fields=["samples"])
        if accepted:
            data["probe_echo_accepted"]=p.get("probe_echo")
        if probe_id:
            data["diagnostic_probe"]={"id":probe_id}
        if accepted or probe_id:
            return JsonResponse(data)
    except Exception:
        # Diagnostics cannot replace, fail or retry a real order response.
        log.exception("Communication probe failed; original polling response retained")
    return response
