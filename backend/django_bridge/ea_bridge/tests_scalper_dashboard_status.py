"""The read-only scalp card must never imply an unverified broker position."""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from django.utils import timezone


@override_settings(RAMON_SCALP_INSTANCE='real', RAMON_SCALP_LOGIN='123', RAMON_SCALP_SERVER='Broker')
class ScalperDashboardStatusTests(SimpleTestCase):
    def test_verified_position_and_stale_telemetry(self):
        feed = SimpleNamespace(instance_id='real', account_login='123', server_name='Broker',
            symbol='GOLD', status='TELEMETRY_OK', terminal_connected=True,
            last_seen=timezone.now(), payload={'server_name':'Broker', 'positions':[{'ticket':'7'}, {'ticket':'8'}]})
        control = SimpleNamespace(automatic=True)
        with patch('ea_bridge.scalper_views.EAHeartbeat.objects.filter') as feeds, \
             patch('ea_bridge.scalper_views.ScalperControl.objects.filter') as controls, \
             patch('ea_bridge.scalper_views.owned_position', side_effect=lambda f,p: object() if p['ticket']=='7' else None) as owned, \
             patch('ea_bridge.scalper_views.readiness', return_value='READY'):
            feeds.return_value.first.return_value = feed
            controls.return_value.first.return_value = control
            response = self.client.get('/api/ea/v1/scalper/status')
            self.assertEqual(response.json(), dict(automatic=True, readiness='READY',
                position_status_known=True, open_positions=1))
            self.assertEqual(response['Cache-Control'], 'no-store')
            feeds.assert_called_with(instance_id='real', account_login='123', server_name='Broker')

            feed.last_seen -= timedelta(minutes=2)
            owned.reset_mock()
            stale = self.client.get('/api/ea/v1/scalper/status').json()
            self.assertFalse(stale['position_status_known'])
            self.assertEqual(stale['open_positions'], 0)
            owned.assert_not_called()

    def test_no_feed_shows_unknown_position_state(self):
        with patch('ea_bridge.scalper_views.EAHeartbeat.objects.filter') as feeds:
            feeds.return_value.first.return_value = None
            response = self.client.get('/api/ea/v1/scalper/status').json()
        self.assertEqual(response, dict(automatic=False, readiness='NO_CONFIGURED_FEED',
            position_status_known=False, open_positions=0))
