from datetime import timedelta
import io
from tempfile import TemporaryDirectory
from unittest.mock import patch
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from .economic_calendar import import_snapshot
from .news_lock import entry_status, major_event
from .tests_economic_calendar import NOW


@override_settings(RAMON_NEWS_LOCK_ENABLED=True, RAMON_NEWS_FEED_AUTOMATIC=True)
class NewsWindowTests(SimpleTestCase):
    def setUp(self):
        self.directory=TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config=override_settings(MEDIA_ROOT=self.directory.name)
        self.config.enable(); self.addCleanup(self.config.disable)
        self.data=dict(provider='ForexFactory',refresh_mode='AUTOMATIC',
            source_url='https://nfs.faireconomy.media/ff_calendar_thisweek.json',
            coverage_start_utc='2026-11-08T05:00:00+00:00',coverage_end_utc='2026-11-15T05:00:00+00:00',
            retrieved_at=NOW.isoformat(),from_date='2026-11-09',
            to_date='2026-11-10',payload=dict(returned=1,totalMatched=1,economicCalendar=[dict(
                event='Core Inflation Rate YoY',time=(NOW+timedelta(minutes=15)).isoformat(),
                country='US',impact='High')]))

    def save(self): import_snapshot(self.data,now=NOW)

    def test_missing_naive_stale_and_no_automatic_feed_fail_closed(self):
        self.assertEqual(entry_status('TREND_PULLBACK',now=NOW)['reason'],'NEWS_CALENDAR_MISSING')
        self.data['provider']='TipRanks'
        self.data['payload']['economicCalendar'][0]['time']='2026-11-09T09:15:00'
        self.save()
        self.assertEqual(entry_status('TREND_PULLBACK',now=NOW)['reason'],'NEWS_TIMEZONE_UNVERIFIED')
        self.assertEqual(entry_status('TREND_PULLBACK',now=NOW+timedelta(hours=2))['reason'],'NEWS_CALENDAR_STALE')
        self.data['provider']='ForexFactory'
        self.data['payload']['economicCalendar'][0]['time']=(NOW+timedelta(minutes=15)).isoformat()
        self.save()
        with override_settings(RAMON_NEWS_FEED_AUTOMATIC=False):
            self.assertEqual(entry_status('TREND_PULLBACK',now=NOW)['reason'],'NEWS_AUTOMATIC_FEED_REQUIRED')

    def test_strict_and_standard_windows_and_boundaries(self):
        self.save()
        self.assertTrue(entry_status('RANGE_EDGE_FADE',now=NOW)['blocked'])
        self.assertFalse(entry_status('TREND_PULLBACK',now=NOW)['blocked'])
        self.assertTrue(entry_status('TREND_PULLBACK',now=NOW+timedelta(minutes=5))['blocked'])
        self.assertTrue(entry_status('SCALPER_M1_M5',now=NOW+timedelta(minutes=45))['blocked'])
        self.assertFalse(entry_status('SCALPER_M1_M5',now=NOW+timedelta(minutes=45,seconds=1))['blocked'])
        self.assertFalse(entry_status('TREND_PULLBACK',now=NOW+timedelta(minutes=30,seconds=1))['blocked'])

    def test_breakout_needs_a_post_window_completed_candle(self):
        self.save()
        now=NOW+timedelta(minutes=31)
        old_bar=(NOW-timedelta(minutes=5)).timestamp()
        new_bar=(NOW+timedelta(minutes=16)).timestamp()
        self.assertEqual(entry_status('SESSION_OPENING_RANGE_BREAKOUT',now=now,
            check_signal=True,signal_bar_time=old_bar)['reason'],'NEWS_FRESH_SIGNAL_REQUIRED')
        self.assertFalse(entry_status('SESSION_OPENING_RANGE_BREAKOUT',now=now,
            check_signal=True,signal_bar_time=new_bar)['blocked'])

    def test_ordinary_events_do_not_lock_and_major_aliases_match(self):
        for name in ['CPI s.a','Core Inflation Rate MoM','Non Farm Payrolls','Core PCE','FOMC Press Conference']:
            self.assertTrue(major_event(name),name)
        self.assertFalse(major_event('Existing Home Sales'))
        self.data['payload']['economicCalendar'][0]['event']='Existing Home Sales'
        self.save()
        self.assertFalse(entry_status('SCALPER_M1_M5',now=NOW)['blocked'])


class NewsExecutionTests(TestCase):
    def setUp(self):
        from .tests_strategy_workers import fixture
        self.feed=fixture()

    @override_settings(RAMON_NEWS_LOCK_ENABLED=True, RAMON_EA_COMMAND_CREATION_ENABLED=True,
                       RAMON_EA_COMMAND_EXECUTION_ENABLED=True)
    def test_creation_block_is_durable_and_no_command_is_created(self):
        from .tests_strategy_workers import open_for
        from .models import EACommand, EAEvent
        with TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            self.assertEqual(open_for('TREND_PULLBACK')[2],'NEWS_CALENDAR_MISSING')
        self.assertFalse(EACommand.objects.exists())
        self.assertEqual(EAEvent.objects.get(event_type='NEWS_ENTRY_BLOCKED').payload['setup'],'TREND_PULLBACK')

    @override_settings(RAMON_NEWS_LOCK_ENABLED=True, RAMON_EA_COMMAND_EXECUTION_ENABLED=True,
                       EA_BEARER_TOKEN='test-token')
    def test_queued_open_is_rejected_but_exit_is_delivered(self):
        from .models import EACommand, EAEvent
        now=timezone.now()
        common=dict(instance_id='demo',account_login='123',symbol='XAUUSD_o',
                    execution_authorized=True,expires_at=now+timedelta(seconds=60))
        entry=EACommand.objects.create(command_id='news-entry',action='OPEN',payload=dict(
            server_name='Demo',engine='STRATEGY',strategy_setup='TREND_PULLBACK'),**common)
        exit=EACommand.objects.create(command_id='news-exit',action='CLOSE',payload=dict(server_name='Demo'),**common)
        with TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory), patch(
            'ea_bridge.profit_protection.create_protection_commands'):
            response=self.client.post('/api/ea/v1/commands/next',data=dict(instance_id='demo',account_login='123'),
                content_type='application/json',HTTP_AUTHORIZATION='Bearer test-token')
        self.assertEqual(response.json()['command']['command_id'],exit.command_id)
        entry.refresh_from_db(); self.assertEqual(entry.state,'REJECTED')
        self.assertEqual(entry.result['detail'],'NEWS_CALENDAR_MISSING')
        self.assertEqual(EAEvent.objects.get(event_type='NEWS_ENTRY_BLOCKED').payload['phase'],'DELIVERY')

    @override_settings(RAMON_NEWS_LOCK_ENABLED=True)
    def test_scalper_creation_also_obeys_the_same_gate(self):
        from .models import ScalperControl, EACommand
        from .scalper_engine import _open_scalp
        control=ScalperControl.objects.create(source='demo',account_login='123',server_name='Demo',
            symbol='XAUUSD_o',automatic=True)
        with TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            command,reason=_open_scalp(self.feed,control,None)
        self.assertIsNone(command)
        self.assertEqual(reason,'NEWS_CALENDAR_MISSING')
        self.assertFalse(EACommand.objects.exists())

    @override_settings(RAMON_NEWS_LOCK_ENABLED=True, RAMON_EA_COMMAND_EXECUTION_ENABLED=True,
                       EA_BEARER_TOKEN='test-token')
    def test_stop_management_is_delivered_with_calendar_missing(self):
        from .models import EACommand
        command=EACommand.objects.create(command_id='news-protection',instance_id='demo',account_login='123',
            symbol='XAUUSD_o',action='MODIFY_SLTP',execution_authorized=True,
            expires_at=timezone.now()+timedelta(seconds=60),payload={'server_name':'Demo'})
        with TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory), patch(
            'ea_bridge.profit_protection.create_protection_commands'):
            response=self.client.post('/api/ea/v1/commands/next',data=dict(instance_id='demo',account_login='123'),
                content_type='application/json',HTTP_AUTHORIZATION='Bearer test-token')
        self.assertEqual(response.json()['command']['command_id'],command.command_id)
