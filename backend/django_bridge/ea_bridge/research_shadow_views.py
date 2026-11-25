import json
from pathlib import Path
from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET
from django.views.decorators.cache import never_cache
from django.utils import timezone
from django.utils.dateparse import parse_datetime

STATE = Path("/shadow-data/forward.json")

@staff_member_required
@require_GET
@never_cache
def page(request):
    return render(request,"ea_bridge/research_shadow.html")

@staff_member_required
@require_GET
@never_cache
def report(request):
    try:
        with STATE.open() as stream:
            data=json.load(stream)
        if data.get("mode")!="PAPER_ONLY" or data.get("live_promotion") is not False:
            raise ValueError("Unexpected state")
        stamp=parse_datetime(data["updated_at"])
        age=(timezone.now()-stamp).total_seconds()
        data["report_age_seconds"]=round(age,1)
        data["report_fresh"]=0<=age<=60
    except (OSError,ValueError,KeyError,TypeError,AttributeError):
        return JsonResponse({"error":"Shadow state unavailable or invalid"},status=503)
    response=JsonResponse(data)
    if request.GET.get("download")=="1":
        response["Content-Disposition"]='attachment; filename="ramon-shadow-report.json"'
    return response
