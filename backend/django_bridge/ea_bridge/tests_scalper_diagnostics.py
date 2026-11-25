from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from .models import EAHeartbeat, ScalpObservation, ScalpAuditEvent, EACommand
from django.utils import timezone
from .scalper_diagnostics import build_report


class FastScalpDiagnosticTests(TestCase):
    def test_staff_only_download_without_feed(self):
        url = reverse('ramon-scalper-diagnostic-report')
        self.assertEqual(self.client.get(url).status_code, 302)
        user = get_user_model().objects.create_user(username='diagnostic-staff', is_staff=True)
        self.client.force_login(user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['readiness'], 'NO_FEED')
        self.assertIn('attachment;', response['Content-Disposition'])
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertEqual(self.client.post(url).status_code, 405)

    def test_scoped_counts_and_no_trade_writes(self):
        now = timezone.now()
        EAHeartbeat.objects.create(instance_id='test-feed', account_login='123',
            server_name='test-server', symbol='XAUUSD_l', status='TELEMETRY_OK')
        scope = dict(source='test-feed', account_login='123', server_name='test-server', symbol='XAUUSD_l')
        for login in ('123','456'):
            ScalpObservation.objects.create(**dict(scope, account_login=login),
                version='test', signal_bar_time=now, recorded_at=now,
                evaluation_start=now, evaluation_end=now, action='WAIT', reason='TEST_WAIT', status='WAIT')
        report = build_report(timezone.now())
        self.assertEqual(report['signal_counts'], [{'action':'WAIT','reason':'TEST_WAIT','count':1}])
        self.assertEqual(EACommand.objects.count(), 0)
        self.assertEqual(ScalpAuditEvent.objects.count(), 0)
        self.assertEqual(ScalpObservation.objects.count(), 2)

    def test_missing_sizing_is_explicit_and_risk_rejections_retained(self):
        now = timezone.now()
        EAHeartbeat.objects.create(instance_id='test-feed', account_login='123',
            server_name='test-server', symbol='XAUUSD_l', status='TELEMETRY_OK')
        scope = dict(source='test-feed', account_login='123', server_name='test-server', symbol='XAUUSD_l')
        ScalpAuditEvent.objects.create(**scope, event_key='test-risk', kind='ENTRY_GATE',
            reason='RISK_BUDGET_BELOW_MINIMUM_VOLUME', observed_at=now, details={})
        report = build_report(timezone.now())
        self.assertEqual(report['risk_rejection_count'], 1)
        self.assertEqual(report['latest_entry_evidence']['sizing_status'], 'UNAVAILABLE')
        self.assertFalse(report['authorization']['main_flag_is_scalper_gate'])
        self.assertEqual(EACommand.objects.count(), 0)
