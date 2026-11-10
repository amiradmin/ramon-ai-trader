"""Bounded refresh of Forex Factory's public weekly JSON calendar export."""
from datetime import datetime, timedelta, timezone as utc_timezone
import hashlib
import json
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
from django.utils import timezone
from .economic_calendar import import_snapshot

SOURCE_URL = 'https://nfs.faireconomy.media/ff_calendar_thisweek.json'
MAX_DOWNLOAD = 1_000_000


def build_snapshot(rows, *, now, cache_age_seconds=0):
    if not isinstance(rows, list) or not 1 <= len(rows) <= 2000:
        raise ValueError('Expected a nonempty complete weekly calendar export')
    eastern = ZoneInfo('America/New_York')
    local = now.astimezone(eastern)
    sunday = local.date()-timedelta(days=(local.weekday()+1)%7)
    start = datetime.combine(sunday, datetime.min.time(), tzinfo=eastern).astimezone(utc_timezone.utc)
    end = datetime.combine(sunday+timedelta(days=7), datetime.min.time(), tzinfo=eastern).astimezone(utc_timezone.utc)
    events = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Invalid calendar row')
        for key in ('country', 'impact', 'title', 'date'):
            if not isinstance(row.get(key), str) or not row[key]:
                raise ValueError('Missing calendar field: '+key)
        stamp = datetime.fromisoformat(row['date'])
        if stamp.utcoffset() is None:
            raise ValueError('Provider timestamp has no explicit UTC offset')
        if not start <= stamp < end:
            raise ValueError('Export does not match the current published week')
        if row['country'] != 'USD' or row['impact'] not in {'High', 'Medium'}:
            continue
        from .news_lock import major_event
        if major_event(row['title']) and stamp.astimezone(eastern).time().replace(tzinfo=None) == datetime.min.time():
            raise ValueError('Major release has a tentative midnight timestamp')
        events.append(dict(event=row['title'], country='US', impact=row['impact'], time=row['date'],
                           actual=None, estimate=None, prev=None, unit='',
                           estimate_text=row.get('forecast') or '', previous_text=row.get('previous') or ''))
    if not events:
        raise ValueError('Weekly export has no US High/Medium events; coverage cannot be certified')
    # Keep the exact source response for later provenance checks, not invented release actuals.
    return dict(provider='ForexFactory', retrieved_at=now.isoformat(), refresh_mode='AUTOMATIC',
                source_url=SOURCE_URL, source_cache_age_seconds=cache_age_seconds, coverage_start_utc=start.isoformat(), coverage_end_utc=end.isoformat(),
                from_date=start.date().isoformat(), to_date=(end-timedelta(microseconds=1)).date().isoformat(),
                source_payload=rows, source_sha256=hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest(),
                payload=dict(returned=len(events), totalMatched=len(events), economicCalendar=events))


def refresh_calendar(*, now=None, opener=urlopen):
    request = Request(SOURCE_URL, headers={'User-Agent':'RamonCalendar/1.0', 'Accept':'application/json',
                                          'Cache-Control':'no-cache'})
    with opener(request, timeout=20) as response:
        if response.status != 200:
            raise ValueError('Calendar HTTP response was not successful')
        data = response.read(MAX_DOWNLOAD+1)
        if len(data) > MAX_DOWNLOAD:
            raise ValueError('Calendar response exceeds size limit')
        # Do not treat an old proxy cache response as a freshly checked schedule.
        cache_age = int(response.headers.get('Age', '0'))
        if not 0 <= cache_age <= 3600:
            raise ValueError('Calendar CDN response is over one hour old')
    now = now or timezone.now()
    snapshot = build_snapshot(json.loads(data), now=now, cache_age_seconds=cache_age)
    normalized = import_snapshot(snapshot, now=now)
    return dict(provider=normalized['provider'], retrieved_at=normalized['retrieved_at'],
                events=len(normalized['events']), coverage_end_utc=snapshot['coverage_end_utc'])


def recent_snapshot(*, now=None):
    """Reuse a recent successful preflight/restart snapshot without relabelling its age."""
    from .economic_calendar import snapshot_path, validate_snapshot, MAX_BYTES
    now = now or timezone.now()
    try:
        path = snapshot_path()
        if path.stat().st_size > MAX_BYTES:
            return None
        normalized = validate_snapshot(json.loads(path.read_text())['raw'], now=now)
        retrieved = datetime.fromisoformat(normalized['retrieved_at'])
        age = (now-retrieved).total_seconds() + normalized.get('source_cache_age_seconds', 0)
        if (normalized['provider'] != 'ForexFactory' or normalized.get('refresh_mode') != 'AUTOMATIC'
            or not normalized['timezone_verified'] or not 0 <= age < 900
            or not datetime.fromisoformat(normalized['coverage_start_utc']) <= now < datetime.fromisoformat(normalized['coverage_end_utc'])):
            return None
        return dict(provider='ForexFactory', retrieved_at=normalized['retrieved_at'],
                    events=len(normalized['events']), coverage_end_utc=normalized['coverage_end_utc'],
                    wait_seconds=900-age)
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        return None
