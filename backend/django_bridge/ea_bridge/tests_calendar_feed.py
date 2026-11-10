from copy import deepcopy
from datetime import timedelta
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from django.test import SimpleTestCase, override_settings
from .calendar_feed import build_snapshot, refresh_calendar
from .economic_calendar import import_snapshot, snapshot_path
from .news_lock import entry_status, major_event
from .tests_economic_calendar import NOW


@override_settings(RAMON_NEWS_LOCK_ENABLED=True, RAMON_NEWS_FEED_AUTOMATIC=True)
class CalendarFeedTests(SimpleTestCase):
    def setUp(self):
        directory=TemporaryDirectory(); self.addCleanup(directory.cleanup)
        config=override_settings(MEDIA_ROOT=directory.name); config.enable(); self.addCleanup(config.disable)
        self.rows=[dict(title='Core CPI m/m',country='USD',impact='High',
                        date='2026-11-10T08:30:00-05:00',forecast='0.2%',previous='0.3%'),
                   dict(title='Other country',country='EUR',impact='High',date='2026-11-10T09:00:00+01:00')]

    def test_automatic_schedule_clears_gate_and_blocks_during_event(self):
        raw=build_snapshot(self.rows,now=NOW)
        normalized=import_snapshot(raw,now=NOW)
        self.assertTrue(normalized['timezone_verified'])
        self.assertEqual(normalized['events'][0]['time_utc'],'2026-11-10T13:30:00+00:00')
        self.assertEqual(normalized['events'][0]['estimate_text'],'0.2%')
        self.assertEqual(len(normalized['events']),1)
        self.assertFalse(entry_status('TREND_PULLBACK',now=NOW)['blocked'])
        from datetime import datetime, timezone
        event_time=datetime(2026,11,10,13,30,tzinfo=timezone.utc)
        import_snapshot(build_snapshot(self.rows,now=event_time),now=event_time)
        self.assertEqual(entry_status('SCALPER_M1_M5',now=event_time)['reason'],'NEWS_EVENT_WINDOW')
        self.assertFalse(entry_status('SCALPER_M1_M5',now=event_time+timedelta(minutes=31))['blocked'])
        self.assertEqual(entry_status('SCALPER_M1_M5',now=event_time+timedelta(hours=2))['reason'],'NEWS_CALENDAR_STALE')
        self.assertTrue(list((snapshot_path().parent/'economic-calendar-archive').glob('*.json')))

    def test_missing_offsets_old_week_empty_and_invalid_data_are_rejected(self):
        for change in ('offset','week','empty','country','midnight'):
            rows=deepcopy(self.rows)
            if change=='offset': rows[0]['date']='2026-11-10T08:30:00'
            elif change=='week': rows[0]['date']='2026-11-03T08:30:00-05:00'
            elif change=='empty': rows=[]
            elif change=='country': rows=[rows[1]]
            else: rows[0]['date']='2026-11-10T00:00:00-05:00'
            with self.subTest(change=change), self.assertRaises(ValueError):
                build_snapshot(rows,now=NOW)

    def test_fetch_failure_does_not_refresh_last_good_schedule(self):
        import_snapshot(build_snapshot(self.rows,now=NOW),now=NOW)
        before=snapshot_path().read_bytes()
        def fail(*args,**kwargs): raise OSError('offline')
        with self.assertRaises(OSError): refresh_calendar(now=NOW+timedelta(hours=1),opener=fail)
        self.assertEqual(before,snapshot_path().read_bytes())

    def test_bounded_fetch_runs_end_to_end_and_rejects_old_cdn_cache(self):
        class Response(io.BytesIO):
            status=200
            headers={}
        response=Response(json.dumps(self.rows).encode())
        self.assertEqual(refresh_calendar(now=NOW,opener=lambda *a,**k:response)['events'],1)
        response=Response(json.dumps(self.rows).encode()); response.headers={'Age':'7200'}
        with self.assertRaises(ValueError): refresh_calendar(now=NOW,opener=lambda *a,**k:response)

    def test_speech_is_not_a_rate_decision_but_federal_funds_rate_is(self):
        self.assertFalse(major_event('FOMC Member Waller Speaks'))
        self.assertFalse(major_event('FOMC Meeting Minutes'))
        self.assertTrue(major_event('Federal Funds Rate'))
        self.assertTrue(major_event('FOMC Statement'))
        self.assertTrue(major_event('Non-Farm Employment Change'))

    def test_weekly_coverage_expires_even_if_snapshot_was_recent(self):
        from datetime import datetime, timezone
        saturday=datetime(2026,11,15,4,50,tzinfo=timezone.utc)
        import_snapshot(build_snapshot(self.rows,now=saturday),now=saturday)
        self.assertEqual(entry_status('TREND_PULLBACK',now=saturday+timedelta(minutes=20))['reason'],
                         'NEWS_CALENDAR_COVERAGE_MISSING')

    def test_restart_reuses_recent_snapshot_without_resetting_freshness(self):
        from .calendar_feed import recent_snapshot
        import_snapshot(build_snapshot(self.rows,now=NOW),now=NOW)
        data=recent_snapshot(now=NOW+timedelta(minutes=5))
        self.assertEqual(data['retrieved_at'],NOW.isoformat())
        self.assertEqual(data['wait_seconds'],600)
        self.assertIsNone(recent_snapshot(now=NOW+timedelta(minutes=16)))

    def test_proxy_cache_age_counts_against_entry_freshness(self):
        raw=build_snapshot(self.rows,now=NOW,cache_age_seconds=3500)
        import_snapshot(raw,now=NOW)
        self.assertFalse(entry_status('TREND_PULLBACK',now=NOW)['blocked'])
        self.assertEqual(entry_status('TREND_PULLBACK',now=NOW+timedelta(seconds=101))['reason'],
                         'NEWS_CALENDAR_STALE')
