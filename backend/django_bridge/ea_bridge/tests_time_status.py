from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.utils import timezone
from .models import EAHeartbeat


@override_settings(RAMON_STRATEGY_INSTANCE='clock-demo', RAMON_STRATEGY_LOGIN='1', RAMON_STRATEGY_SERVER='Demo')
class TimeStatusTests(TestCase):
    url='/api/ea/v1/time-status'

    def test_missing_feed_is_explicit_and_get_is_read_only(self):
        data=self.client.get(self.url).json()
        self.assertEqual(data['internal_timezone'],'UTC')
        self.assertTrue(data['timezone_aware'])
        self.assertEqual(data['state'],'NO_CONFIGURED_FEED')
        self.assertEqual(self.client.post(self.url).status_code,405)
        self.assertFalse(EAHeartbeat.objects.exists())

    def test_different_timezone_represents_same_instant_and_future_times_warn(self):
        from datetime import timezone as zone
        now=timezone.now()
        feed=EAHeartbeat.objects.create(instance_id='clock-demo',account_login='1',server_name='Demo',
            payload={'quote_time':now.astimezone(zone(timedelta(hours=3,minutes=30))).isoformat()})
        with patch('ea_bridge.time_status.timezone.now',return_value=now+timedelta(seconds=1)):
            response=self.client.get(self.url)
        self.assertEqual(response.json()['state'],'TIME_ALIGNED')
        self.assertTrue(response.json()['quote_time_utc'].endswith('+00:00'))
        self.assertEqual(response['Cache-Control'],'no-store')
        feed.payload['quote_time']=(now+timedelta(hours=1)).isoformat(); feed.save()
        self.assertEqual(self.client.get(self.url).json()['state'],'FUTURE_TIMESTAMP')
        feed.payload['quote_time']='2026-11-10T11:00:00'; feed.save()
        self.assertEqual(self.client.get(self.url).json()['state'],'QUOTE_TIME_UNVERIFIED')

    def test_stale_quotes_are_visible(self):
        EAHeartbeat.objects.create(instance_id='clock-demo',account_login='1',server_name='Demo',
            payload={'quote_time':(timezone.now()-timedelta(minutes=2)).isoformat()})
        self.assertEqual(self.client.get(self.url).json()['state'],'STALE_FEED')
