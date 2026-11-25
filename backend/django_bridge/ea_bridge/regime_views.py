from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET
from django.views.decorators.cache import never_cache
from .regime_coordinator import build_report

@staff_member_required
@require_GET
@never_cache
def page(request):
    return render(request,"ea_bridge/regime_dashboard.html")

@staff_member_required
@require_GET
@never_cache
def report(request):
    source,login,server,symbol=[getattr(settings,k,"") for k in ("RAMON_STRATEGY_INSTANCE","RAMON_STRATEGY_LOGIN","RAMON_STRATEGY_SERVER","RAMON_REAL_CENT_SYMBOL")]
    if not all((source,login,server,symbol)):
        return JsonResponse({"error":"Configured account scope unavailable"},status=503)
    response=JsonResponse(build_report(source,symbol,login,server))
    if request.GET.get("download")=="1":
        response["Content-Disposition"]='attachment; filename="regime-coordinator-v12.json"'
    return response
