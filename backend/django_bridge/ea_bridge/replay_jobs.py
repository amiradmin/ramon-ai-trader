"""Disk-backed local research queue shared through the existing /app/media volume.

This queue never calls EA commands. POST uses Django CSRF, jobs are random UUIDs,
and work happens in a separate limited worker process, not in Gunicorn.
"""
import json
import os
from pathlib import Path
import re
import time
import uuid

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

from .replay_backtest import _inputs

JOB_ID = re.compile(r"^[0-9a-f]{32}$")
ROOT = Path(settings.MEDIA_ROOT) / "historical-replay-jobs"

def path_for(job_id):
    if not JOB_ID.fullmatch(job_id):
        raise ValueError("Invalid job identifier")
    return ROOT / (job_id + ".json")

def store(job_id, value):
    ROOT.mkdir(parents=True, exist_ok=True)
    target = path_for(job_id)
    temp = ROOT / (job_id + "." + uuid.uuid4().hex + ".tmp")
    with temp.open("x", encoding="utf-8") as out:
        json.dump(value, out, separators=(",", ":"), allow_nan=False)
    os.replace(temp, target)

def read(job_id):
    with path_for(job_id).open(encoding="utf-8") as f:
        return json.load(f)

@require_POST
def create_job(request):
    try:
        # Reuse the existing explicit broker specifications and UTC date validation.
        from types import SimpleNamespace
        data = _inputs(SimpleNamespace(GET=request.POST))
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    ROOT.mkdir(parents=True, exist_ok=True)
    # One unfinished job per local deployment, preventing accidental saturation.
    for file in ROOT.glob("*.json"):
        try:
            job = json.loads(file.read_text(encoding="utf-8"))
            if job.get("status") in ("QUEUED", "RUNNING"):
                return JsonResponse({"error":"A historical backtest is already queued or running.","job_id":job["id"]},status=409)
        except (ValueError, OSError, KeyError):
            continue
    job_id = uuid.uuid4().hex
    store(job_id, {
        "id":job_id,"status":"QUEUED","progress_pct":0,
        "created_at":time.time(),"updated_at":time.time(),
        "params":{**data,"start":data["start"].isoformat(),"end":data["end"].isoformat()},
        "compare_balances":request.POST.get("compare_balances")=="1" and data["setups"] != ["FAST_SCALP"],
        "result":None,"error":None,
    })
    response = JsonResponse({"job_id":job_id,"status":"QUEUED"}, status=202)
    response["Cache-Control"] = "no-store"
    return response

@require_GET
def job_status(request, job_id):
    try:
        job = read(job_id)
    except (ValueError, FileNotFoundError):
        return JsonResponse({"error":"Job not found"},status=404)
    # Never expose the absolute filesystem path, credentials, or internal params.
    answer={key:job.get(key) for key in ("id","status","progress_pct","error","result")}
    response=JsonResponse(answer)
    response["Cache-Control"]="no-store"
    return response
