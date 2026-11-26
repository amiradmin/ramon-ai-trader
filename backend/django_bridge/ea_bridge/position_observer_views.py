from django.contrib.admin.views.decorators import staff_member_required
from django.views.decorators.http import require_GET
from django.views.decorators.cache import never_cache
from django.http import JsonResponse
from django.shortcuts import render
from .position_observer import build_report

@staff_member_required
@require_GET
@never_cache
def page(request):
    return render(request,"ea_bridge/position_observer.html")

@staff_member_required
@require_GET
@never_cache
def report(request):
    response=JsonResponse(build_report(),json_dumps_params={"allow_nan":False})
    if request.GET.get("download")=="1":
        response["Content-Disposition"]='attachment; filename="position-observer-v15.1.json"'
    return response
