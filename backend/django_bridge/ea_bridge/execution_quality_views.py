from django.contrib.admin.views.decorators import staff_member_required
from django.views.decorators.http import require_GET
from django.views.decorators.cache import never_cache
from django.http import JsonResponse
from django.shortcuts import render
from .execution_quality import build_report

@staff_member_required
@require_GET
@never_cache
def page(request):
    return render(request,"ea_bridge/execution_quality.html")

@staff_member_required
@require_GET
@never_cache
def report(request):
    try:
        data=build_report(int(request.GET.get("days","30")))
    except ValueError as exc:
        return JsonResponse({"error":str(exc)},status=400)
    response=JsonResponse(data,json_dumps_params={"allow_nan":False})
    if request.GET.get("download")=="1":
        response["Content-Disposition"]='attachment; filename="execution-quality-v14.4.json"'
    return response
