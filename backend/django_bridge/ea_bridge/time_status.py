"""Read-only UTC/quote diagnostics; never infer a broker timezone from its name."""
from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET
from .strategy_worker import configured_feed


@require_GET
def time_status(request):
    now = timezone.now()
    feed = configured_feed()
    result = dict(server_utc=now.isoformat(), internal_timezone=settings.TIME_ZONE,
                  timezone_aware=settings.USE_TZ, state='NO_CONFIGURED_FEED',
                  quote_time_utc=None, quote_age_seconds=None, telemetry_age_seconds=None,
                  broker_calendar_day=None, broker_timezone='NOT_REPORTED',
                  sessions={'London': 'Europe/London', 'New York': 'America/New_York'})
    if feed:
        payload = feed.payload if isinstance(feed.payload, dict) else {}
        result['broker_calendar_day'] = payload.get('broker_day')
        result['telemetry_age_seconds'] = round((now-feed.last_seen).total_seconds(), 3)
        try:
            quote = parse_datetime(str(payload.get('quote_time', '')))
            if not quote or not timezone.is_aware(quote):
                raise ValueError('Explicit quote timezone required')
            from datetime import timezone as utc_timezone
            result['quote_time_utc'] = quote.astimezone(utc_timezone.utc).isoformat()
            result['quote_age_seconds'] = round((now-quote).total_seconds(), 3)
            if result['telemetry_age_seconds'] < 0 or result['quote_age_seconds'] < -5:
                result['state'] = 'FUTURE_TIMESTAMP'
            elif result['telemetry_age_seconds'] > 60 or result['quote_age_seconds'] > 30:
                result['state'] = 'STALE_FEED'
            else:
                result['state'] = 'TIME_ALIGNED'
        except (ValueError, TypeError, OverflowError):
            result['state'] = 'QUOTE_TIME_UNVERIFIED'
    response = JsonResponse(result)
    response['Cache-Control'] = 'no-store'
    return response
