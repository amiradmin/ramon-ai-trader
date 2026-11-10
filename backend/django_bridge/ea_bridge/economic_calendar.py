"""Validated, read-only event snapshots imported from a connected research tool.

This module has no dependency on the decision pipeline. Provider timestamps
without offsets remain unverified; importing a snapshot does not create a feed.
"""
from datetime import datetime, timedelta, timezone as dt_timezone
import json
import math
from pathlib import Path
import tempfile
import os

from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

MAX_BYTES = 512_000
MAX_AGE = timedelta(hours=24)


def snapshot_path():
    return Path(settings.MEDIA_ROOT) / "research" / "economic-calendar.json"


def validate_snapshot(data, *, now):
    if not isinstance(data, dict) or data.get("provider") != "TipRanks":
        raise ValueError("A TipRanks snapshot envelope is required")
    retrieved = datetime.fromisoformat(data["retrieved_at"])
    if retrieved.utcoffset() is None or retrieved > now + timedelta(minutes=1):
        raise ValueError("retrieved_at must have a timezone and cannot be in the future")
    payload = data["payload"]
    rows = payload["economicCalendar"]
    if not isinstance(rows, list) or len(rows) > 200:
        raise ValueError("At most 200 calendar events are supported")
    # Never turn a limited/filtered response into a claim of complete coverage.
    if payload.get("returned") != len(rows) or payload.get("totalMatched") != len(rows):
        raise ValueError("Incomplete calendar response; retrieve the full selected range")
    start = datetime.fromisoformat(data["from_date"]).date()
    end = datetime.fromisoformat(data["to_date"]).date()
    if not 0 <= (end-start).days <= 31:
        raise ValueError("Invalid calendar coverage")
    events, seen = [], set()
    for row in rows:
        stamp = datetime.fromisoformat(row["time"])
        name, impact = row["event"], row["impact"]
        if row["country"] != "US" or impact not in {"High", "Medium"}:
            raise ValueError("Only the requested US High/Medium scope is accepted")
        if not isinstance(name, str) or not 1 <= len(name) <= 240 or not start <= stamp.date() <= end:
            raise ValueError("Invalid event name or date outside snapshot coverage")
        identity = (name, row["time"], row["country"])
        if identity in seen:
            continue
        seen.add(identity)
        unit = row.get("unit") or ""
        if not isinstance(unit, str) or len(unit) > 32:
            raise ValueError("Invalid event unit")
        values = {}
        for key in ("actual", "estimate", "prev"):
            value = row.get(key)
            if value is not None and (type(value) not in {int, float} or not math.isfinite(value)):
                raise ValueError("Event values must be finite numbers or null")
            values[key] = value
        events.append({"name": name, "impact": impact, "country": "US", "provider_time": row["time"],
                       "time_utc": stamp.astimezone(dt_timezone.utc).isoformat() if stamp.utcoffset() is not None else None,
                       "unit": unit.strip(), **values})
    return {"provider": "TipRanks", "retrieved_at": retrieved.isoformat(),
            "from_date": start.isoformat(), "to_date": end.isoformat(),
            "events": events, "timezone_verified": all(e["time_utc"] is not None for e in events)}


def import_snapshot(data, *, now=None):
    now = now or timezone.now()
    normalized = validate_snapshot(data, now=now)
    path = snapshot_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Validate everything before replacing the last good snapshot, atomically.
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False, encoding="utf-8") as handle:
        temporary = handle.name
        try:
            json.dump({"raw": data, "normalized": normalized}, handle, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            os.unlink(temporary)
            raise
    try:
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return normalized


@require_GET
def calendar_status(request):
    now = timezone.now()
    result = {"mode": "CONTEXT_ONLY", "affects_orders": False, "automatic_refresh": False,
              "server_time": now.isoformat(), "events": [], "state": "NOT_IMPORTED"}
    path = snapshot_path()
    try:
        if path.stat().st_size > MAX_BYTES:
            raise ValueError("oversize snapshot")
        data = json.loads(path.read_text(encoding="utf-8"))
        snapshot = validate_snapshot(data["raw"], now=now)
        result.update(snapshot)
        age = now-datetime.fromisoformat(snapshot["retrieved_at"])
        result["age_seconds"] = max(0, int(age.total_seconds()))
        if age > MAX_AGE or now.date().isoformat() > snapshot["to_date"]:
            result["state"] = "STALE"
        elif not snapshot["timezone_verified"]:
            result["state"] = "TIMEZONE_UNVERIFIED"
        else:
            result["state"] = "READY"
            result["events"] = [e for e in snapshot["events"] if datetime.fromisoformat(e["time_utc"]) >= now]
            result["events"].sort(key=lambda e: e["time_utc"])
    except FileNotFoundError:
        pass
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        result.update(state="UNAVAILABLE", events=[])
    response = JsonResponse(result, json_dumps_params={"allow_nan": False})
    response["Cache-Control"] = "no-store"
    return response
