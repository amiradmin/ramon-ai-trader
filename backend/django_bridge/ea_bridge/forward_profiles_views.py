import json
from pathlib import Path
from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET
from django.views.decorators.cache import never_cache
from django.utils import timezone
from django.utils.dateparse import parse_datetime

STATE=Path("/shadow-data/forward-v15.3.json")

@staff_member_required
@require_GET
@never_cache
def page(request):
    return render(request,"ea_bridge/forward_profiles.html")

@staff_member_required
@require_GET
@never_cache
def report(request):
    try:
        data=json.loads(STATE.read_text())
        if (data.get("version")!="V15.3" or data.get("mode")!="PAPER_ONLY"
            or data.get("affects_orders") is not False or data.get("live_promotion") is not False
            or not isinstance(data.get("books"),dict) or not isinstance(data.get("profiles"),dict)
            or set(data["books"])!=set(data["profiles"])):
            raise ValueError("Unexpected cohort")
        stamp=parse_datetime(data["updated_at"])
        age=(timezone.now()-stamp).total_seconds()
        data["report_age_seconds"]=round(age,1)
        data["report_fresh"]=0<=age<=60
        response=JsonResponse(data,json_dumps_params={"allow_nan":False})
    except (OSError,ValueError,KeyError,TypeError,AttributeError):
        return JsonResponse({"error":"Forward cohort unavailable or invalid"},status=503)
    if request.GET.get("download")=="1":
        response["Content-Disposition"]='attachment; filename="forward-report-v15.3.json"'
    return response
