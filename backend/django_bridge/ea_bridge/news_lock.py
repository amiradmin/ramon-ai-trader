"""Entry-only macro gate. Unknown calendar state fails closed; exits bypass it."""
from datetime import datetime, timedelta
import hashlib
import json
import re
from django.conf import settings
from django.utils import timezone
from .economic_calendar import snapshot_path, validate_snapshot, MAX_BYTES

STRICT = {'SCALPER_M1_M5', 'RANGE_EDGE_FADE', 'FAILED_BREAKOUT_SWEEP', 'EXHAUSTION_REVERSAL'}


def major_event(name):
    name = re.sub(r'[^a-z0-9]+', ' ', name.lower())
    releases = bool(re.search(r'\b(cpi|nonfarm|non farm|payrolls?|nfp|pce)\b', name)
                    or 'inflation rate' in name or 'personal consumption expenditure' in name
                    or 'personal income and outlays' in name or 'employment situation' in name
                    or 'federal funds rate' in name)
    fomc = 'fomc' in name and any(term in name for term in ('statement', 'press conference', 'meeting', 'rate', 'economic projections')) and 'minutes' not in name
    return releases or fomc or ('fed' in name and ('interest rate' in name or 'press conference' in name))



def entry_status(setup, *, now=None, check_signal=False, signal_bar_time=None):
    now = now or timezone.now()
    before, after = (15, 30) if setup in STRICT else (10, 15)
    result = dict(enabled=bool(settings.RAMON_NEWS_LOCK_ENABLED), blocked=False,
                  reason='NEWS_LOCK_DISABLED', setup=setup, before_minutes=before, after_minutes=after)
    if not result['enabled']:
        return result
    def blocked(reason, **details):
        return dict(result, blocked=True, reason=reason, **details)
    try:
        path = snapshot_path()
        if path.stat().st_size > MAX_BYTES:
            return blocked('NEWS_CALENDAR_INVALID')
        raw = json.loads(path.read_text(encoding='utf-8'))['raw']
        calendar = validate_snapshot(raw, now=now)
        result['retrieved_at'] = calendar['retrieved_at']
        result['snapshot_sha256'] = hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()
        age = (now-datetime.fromisoformat(calendar['retrieved_at'])).total_seconds() + calendar.get('source_cache_age_seconds', 0)
        if age < 0 or age > settings.RAMON_NEWS_MAX_AGE_SECONDS:
            return blocked('NEWS_CALENDAR_STALE')
        if not calendar['from_date'] <= now.date().isoformat() <= calendar['to_date']:
            return blocked('NEWS_CALENDAR_COVERAGE_MISSING')
        if calendar.get('coverage_end_utc') and not datetime.fromisoformat(calendar['coverage_start_utc']) <= now < datetime.fromisoformat(calendar['coverage_end_utc']):
            return blocked('NEWS_CALENDAR_COVERAGE_MISSING')
        if not calendar['timezone_verified']:
            return blocked('NEWS_TIMEZONE_UNVERIFIED')
        if not settings.RAMON_NEWS_FEED_AUTOMATIC or calendar.get('refresh_mode') != 'AUTOMATIC' or calendar['provider'] != 'ForexFactory':
            return blocked('NEWS_AUTOMATIC_FEED_REQUIRED')
        windows = []
        ended = []
        for event in calendar['events']:
            if not major_event(event['name']):
                continue
            event_time = datetime.fromisoformat(event['time_utc'])
            start, end = event_time-timedelta(minutes=before), event_time+timedelta(minutes=after)
            if end < now:
                ended.append(end)
            if start <= now <= end:
                windows.append(dict(name=event['name'], event_time=event['time_utc'],
                                    start=start.isoformat(), end=end.isoformat()))
        if windows:
            return blocked('NEWS_EVENT_WINDOW', events=windows)
        if check_signal and ended and setup in {'SESSION_OPENING_RANGE_BREAKOUT', 'VOLATILITY_COMPRESSION_EXPANSION'}:
            try:
                closed_at = datetime.fromtimestamp(float(signal_bar_time), tz=now.tzinfo)+timedelta(minutes=15)
                if closed_at <= max(ended):
                    return blocked('NEWS_FRESH_SIGNAL_REQUIRED')
            except (TypeError, ValueError, OverflowError, OSError):
                return blocked('NEWS_FRESH_SIGNAL_REQUIRED')
        return dict(result, reason='NEWS_CLEAR')
    except FileNotFoundError:
        return blocked('NEWS_CALENDAR_MISSING')
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        return blocked('NEWS_CALENDAR_INVALID')


def audit_block(feed, status, *, phase, command_id='', now=None):
    if not status['blocked']:
        return
    from .models import EAEvent
    now = now or timezone.now()
    identity = [feed.instance_id, feed.account_login, feed.server_name, feed.symbol,
                status['setup'], status['reason'], phase, command_id, int(now.timestamp())//60]
    key = 'news-lock-' + hashlib.sha256(json.dumps(identity).encode()).hexdigest()
    EAEvent.objects.get_or_create(event_id=key, defaults=dict(instance_id=feed.instance_id,
        event_type='NEWS_ENTRY_BLOCKED', occurred_at=now,
        payload=dict(status, account_login=feed.account_login, server_name=feed.server_name,
                     symbol=feed.symbol, phase=phase, command_id=command_id)))
