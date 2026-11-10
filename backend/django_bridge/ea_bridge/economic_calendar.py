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
    if not isinstance(data, dict) or data.get("provider") not in {"TipRanks", "ForexFactory"}:
        raise ValueError("A supported calendar snapshot envelope is required")
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
    coverage = {}
    if data['provider'] == 'ForexFactory':
        if data.get('source_url') != 'https://nfs.faireconomy.media/ff_calendar_thisweek.json' or data.get('refresh_mode') != 'AUTOMATIC':
            raise ValueError('Forex Factory snapshots require the automatic weekly export source')
        beginning = datetime.fromisoformat(data['coverage_start_utc'])
        ending = datetime.fromisoformat(data['coverage_end_utc'])
        if beginning.utcoffset() is None or ending.utcoffset() is None or not timedelta(days=6) < ending-beginning < timedelta(days=8):
            raise ValueError('Invalid weekly coverage bounds')
        cache_age = data.get('source_cache_age_seconds', 0)
        if type(cache_age) is not int or not 0 <= cache_age <= 3600:
            raise ValueError('Invalid source cache age')
        coverage = dict(coverage_start_utc=beginning.isoformat(), coverage_end_utc=ending.isoformat(),
                        source_cache_age_seconds=cache_age)
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
        text_values = {}
        for key in ('estimate_text', 'previous_text'):
            value = row.get(key)
            if value is not None:
                if not isinstance(value, str) or len(value) > 64:
                    raise ValueError('Invalid provider value text')
                text_values[key] = value
        if coverage and (stamp.utcoffset() is None or not beginning <= stamp < ending):
            raise ValueError('Forex Factory event outside verified weekly coverage')
        events.append({"name": name, "impact": impact, "country": "US", "provider_time": row["time"],
                       "time_utc": stamp.astimezone(dt_timezone.utc).isoformat() if stamp.utcoffset() is not None else None,
                       "unit": unit.strip(), **values, **text_values})
    return {"provider": data["provider"], "retrieved_at": retrieved.isoformat(),
            "refresh_mode": data.get("refresh_mode", "MANUAL"), **coverage,
            "from_date": start.isoformat(), "to_date": end.isoformat(),
            "events": events, "timezone_verified": all(e["time_utc"] is not None for e in events)}


def import_snapshot(data, *, now=None):
    now = now or timezone.now()
    normalized = validate_snapshot(data, now=now)
    # Preserve every successful source snapshot before publishing the latest view.
    import hashlib
    encoded = json.dumps({'raw': data, 'normalized': normalized}, sort_keys=True, allow_nan=False)
    if len(encoded.encode()) > MAX_BYTES:
        raise ValueError('Normalized snapshot exceeds size limit')
    archive = snapshot_path().parent / 'economic-calendar-archive'
    archive.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    archived = archive / (digest+'.json')
    if not archived.exists():
        with archived.open('x', encoding='utf-8') as stream:
            stream.write(encoded)
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
        age = now-datetime.fromisoformat(snapshot["retrieved_at"]) + timedelta(seconds=snapshot.get("source_cache_age_seconds", 0))
        result["age_seconds"] = max(0, int(age.total_seconds()))
        result['automatic_refresh'] = bool(settings.RAMON_NEWS_FEED_AUTOMATIC and snapshot.get('refresh_mode') == 'AUTOMATIC' and snapshot['provider'] == 'ForexFactory')
        out_of_week = snapshot.get('coverage_end_utc') and not datetime.fromisoformat(snapshot['coverage_start_utc']) <= now < datetime.fromisoformat(snapshot['coverage_end_utc'])
        max_age = timedelta(seconds=settings.RAMON_NEWS_MAX_AGE_SECONDS) if result['automatic_refresh'] else MAX_AGE
        if age > max_age or out_of_week or now.date().isoformat() > snapshot["to_date"]:
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
    from .news_lock import entry_status
    from .strategy_registry import STRATEGIES
    result['entry_locks'] = {setup: entry_status(setup, now=now) for setup in [*STRATEGIES, 'SCALPER_M1_M5']}
    if settings.RAMON_NEWS_LOCK_ENABLED:
        result.update(mode='ENTRY_GATE_AND_CONTEXT', affects_orders=True)
    response = JsonResponse(result, json_dumps_params={"allow_nan": False})
    response["Cache-Control"] = "no-store"
    return response
