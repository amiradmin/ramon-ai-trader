from datetime import timedelta
from copy import deepcopy
from django.db import connection, transaction, DatabaseError
from django.test import TestCase
from django.utils import timezone
from .models import DecisionEvaluation, EACommand, EAHeartbeat, RiskVerdictRecord, ReliabilityIncident
from .reliability import reconcile, record_telemetry_gap


class ReliabilityTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.feed = EAHeartbeat.objects.create(instance_id='demo', account_login='123', server_name='Demo',
            symbol='GOLD', terminal_connected=True, status='TELEMETRY_OK', payload={
                'quote_time': self.now.isoformat(), 'symbol_info': {'tick_size': '.01'},
                'positions': [{'ticket': '42', 'symbol': 'GOLD', 'type': 0, 'volume': .02, 'sl': 90, 'tp': 112.5}]})
        self.feed.last_seen = self.now
        self.command = EACommand.objects.create(command_id='open', instance_id='demo', account_login='123',
            symbol='GOLD', action='OPEN', state='EXECUTED', completed_at=self.now-timedelta(seconds=5),
            expires_at=self.now, payload={'server_name': 'Demo', 'side': 'BUY', 'volume': .02,
            'stop_loss': 90, 'take_profit': 112.5}, result={'broker_ticket': '42'})

    def test_later_telemetry_confirms_and_detects_changed_stops(self):
        self.assertEqual(reconcile(self.command, self.feed, self.now)['verification'], 'CONFIRMED_CURRENT_POSITION')
        self.feed.payload['positions'][0]['sl'] = 85
        self.assertEqual(reconcile(self.command, self.feed, self.now)['verification'], 'POSITION_MISMATCH')
        self.command.action = 'MODIFY_SLTP'
        self.command.payload['position_ticket'] = 42
        self.command.payload['stop_loss'] = 100
        self.feed.payload['positions'][0]['sl'] = 100
        self.assertEqual(reconcile(self.command, self.feed, self.now)['verification'], 'CONFIRMED_CURRENT_POSITION')

    def test_stale_scope_and_pre_ack_snapshots_cannot_confirm(self):
        self.feed.last_seen = self.now-timedelta(minutes=5)
        self.assertEqual(reconcile(self.command, self.feed, self.now)['verification'], 'TELEMETRY_STALE')
        self.feed.last_seen = self.now
        self.feed.payload['quote_time'] = (self.now-timedelta(minutes=5)).isoformat()
        self.assertEqual(reconcile(self.command, self.feed, self.now)['verification'], 'QUOTE_STALE')
        self.feed.payload['quote_time'] = self.now.isoformat()
        self.feed.last_seen = self.now-timedelta(seconds=10)
        self.assertEqual(reconcile(self.command, self.feed, self.now)['verification'], 'AWAITING_LATER_TELEMETRY')
        self.command.payload['server_name'] = 'Other'
        self.assertEqual(reconcile(self.command, self.feed, self.now)['verification'], 'SCOPE_MISMATCH')

    def test_missing_position_is_not_assumed_successful_close(self):
        self.feed.payload['positions'] = []
        self.command.action = 'CLOSE'
        self.command.payload['position_ticket'] = 42
        self.assertEqual(reconcile(self.command, self.feed, self.now)['verification'], 'POSITION_ABSENT_CLOSE_UNPROVEN')
        self.command.state = 'RECONCILIATION_REQUIRED'
        self.assertEqual(reconcile(self.command, self.feed, self.now)['verification'], 'REQUIRES_REVIEW')

    def test_gap_is_scoped_and_durable_on_recovery(self):
        self.feed.last_seen = self.now-timedelta(minutes=3)
        record_telemetry_gap(self.feed, 'other', 'Demo')
        self.assertEqual(ReliabilityIncident.objects.count(), 0)
        record_telemetry_gap(self.feed, '123', 'Demo')
        self.assertEqual(ReliabilityIncident.objects.get().kind, 'TELEMETRY_GAP')

    def test_evaluation_snapshot_survives_later_evaluation(self):
        features = {'market_snapshot': {'ask': 100}}
        def create(action):
            verdict = RiskVerdictRecord.objects.create(decision_id='same', approved=action=='BUY', reason_code=action)
            return DecisionEvaluation.objects.create(decision_id='same', instance_id='demo', account_login='123',
                server_name='Demo', symbol='GOLD', action=action, rationale_code=action, confidence=.7,
                model_version='v1', features=deepcopy(features), verdict=verdict)
        first = create('BUY')
        self.command.evaluation = first; self.command.save()
        features['market_snapshot']['ask'] = 110
        create('WAIT')
        first.refresh_from_db()
        self.assertEqual(first.action, 'BUY')
        self.assertEqual(first.features['market_snapshot']['ask'], 100)
        with self.assertRaises(ValueError): first.save()
        with self.assertRaises(ValueError): first.delete()
        with self.assertRaises(ValueError): DecisionEvaluation.objects.filter(pk=first.pk).update(action='WAIT')
        with self.assertRaises(DatabaseError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("UPDATE ea_bridge_decisionevaluation SET action = %s WHERE id = %s", ['WAIT', first.pk])


    def test_dashboard_get_does_not_mutate_or_retry_commands(self):
        before = (EACommand.objects.count(), ReliabilityIncident.objects.count())
        response = self.client.get('/api/ea/v1/reliability')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['affects_orders'])
        self.assertEqual(before, (EACommand.objects.count(), ReliabilityIncident.objects.count()))

    def test_stale_quote_transition_is_recorded_once_then_recovers(self):
        stale = (self.now-timedelta(minutes=5)).isoformat()
        record_telemetry_gap(self.feed, '123', 'Demo', stale)
        self.assertEqual(ReliabilityIncident.objects.get().kind, 'QUOTE_STALE')
        self.feed.payload['quote_time'] = stale
        record_telemetry_gap(self.feed, '123', 'Demo', stale)
        self.assertEqual(ReliabilityIncident.objects.count(), 1)
        record_telemetry_gap(self.feed, '123', 'Demo', self.now.isoformat())
        self.assertEqual(ReliabilityIncident.objects.order_by('-pk').first().kind, 'QUOTE_RECOVERED')
