from datetime import timedelta
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.db import connection,transaction,DatabaseError
from django.test import TestCase,override_settings

from . import tests_scalping as fixtures
from .models import ScalpAuditEvent,ScalpObservation,ScalperControl,EACommand
from .scalp_observer import record_scalp
from .scalper_audit import health_skip,command_event,checksum
from .scalper_engine import open_scalp


@override_settings(RAMON_SCALP_SHADOW_ENABLED=True)
class ScalpAuditTests(TestCase):
    def setUp(self):
        fixtures.ScalpObserverTests.setUp(self)

    def record(self):
        record_scalp(self.feed,now=self.now)
        return ScalpObservation.objects.get()

    def export(self,path,cutoff):
        call_command('export_scalper_journal',source='demo',account_login='123',server='Demo',symbol='GOLD',
                     output=str(path),as_of=cutoff.isoformat(),stdout=StringIO())
        return [json.loads(line) for line in path.read_text().splitlines()]

    def test_source_archive_receipts_and_input_checksum_are_preserved(self):
        row=self.record();snap=row.snapshot
        self.assertEqual(snap['provenance']['schema_version'],2)
        self.assertEqual(checksum(snap['inputs']),snap['input_sha256'])
        self.assertEqual(len(snap['input_received_at']['M1']),64)
        artifact=ScalpAuditEvent.objects.get(event_key=snap['policy_artifact_key'])
        self.assertIn('scalping.py',artifact.details['source_texts'])
        self.assertEqual(snap['raw_signal'],snap['plan'])

    def test_gate_results_and_health_events_are_durable_and_deduplicated(self):
        row=self.record()
        control=ScalperControl.objects.create(source='demo',account_login='123',server_name='Demo',symbol='GOLD')
        for _ in range(2): open_scalp(self.feed,control,row,now=self.now)
        self.assertEqual(ScalpAuditEvent.objects.filter(kind='ENTRY_GATE',reason='AUTOMATIC_PAUSED').count(),1)
        for _ in range(2): health_skip(self.feed,'QUOTE_STALE',self.now)
        self.assertEqual(ScalpAuditEvent.objects.filter(kind='DATA_UNAVAILABLE').count(),1)
        self.assertEqual(EACommand.objects.count(),0)

    def test_database_blocks_input_and_terminal_label_rewrites(self):
        row=self.record()
        with self.assertRaises(DatabaseError),transaction.atomic():
            ScalpObservation.objects.filter(pk=row.pk).update(snapshot={})
        ScalpObservation.objects.filter(pk=row.pk).update(status='SCORED',outcome={'net_points':1,'net_r':.1},settled_at=self.now+timedelta(minutes=5))
        with self.assertRaises(DatabaseError),transaction.atomic():
            ScalpObservation.objects.filter(pk=row.pk).update(outcome={'net_points':999})
        event=ScalpAuditEvent.objects.first()
        with self.assertRaises(ValueError): event.delete()
        with self.assertRaises(DatabaseError),transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('UPDATE ea_bridge_scalpauditevent SET reason=%s WHERE id=%s',['changed',event.pk])

    def test_export_keeps_future_labels_out_and_waits_unlabelled(self):
        row=self.record()
        ScalpObservation.objects.filter(pk=row.pk).update(status='SCORED',outcome={'net_points':1,'net_r':.1},settled_at=self.now+timedelta(minutes=5))
        with TemporaryDirectory() as directory:
            early=self.export(Path(directory)/'early.jsonl',self.now+timedelta(minutes=1))
            e=next(x for x in early if x['type']=='evaluation')
            self.assertIsNone(e['labels']['paper'])
            self.assertEqual(e['status_as_of'],'WAITING_AS_OF_CUTOFF')
            self.assertFalse(e['paper_training_eligible'])
            late=self.export(Path(directory)/'late.jsonl',self.now+timedelta(minutes=6))
            e=next(x for x in late if x['type']=='evaluation')
            self.assertTrue(e['paper_training_eligible'],e['quality_issues'])
            self.assertNotIn('outcome',e['features'])

    def test_export_does_not_mix_symbols(self):
        first=self.record()
        ScalpObservation.objects.create(source='demo',account_login='123',server_name='Demo',symbol='OTHER',
            version=first.version,signal_bar_time=first.signal_bar_time,recorded_at=first.recorded_at,
            action='WAIT',reason='TEST',status='NOT_ENTERED',evaluation_start=first.evaluation_start,
            evaluation_end=first.evaluation_end,snapshot={})
        with TemporaryDirectory() as directory:
            rows=self.export(Path(directory)/'scoped.jsonl',self.now+timedelta(seconds=1))
            self.assertEqual(sum(r['type']=='evaluation' for r in rows),1)

    def test_broker_ack_transitions_have_separate_immutable_events(self):
        row=self.record()
        command=EACommand.objects.create(command_id='scalp-test',instance_id='demo',account_login='123',
            symbol='GOLD',action='OPEN',state='CLAIMED',expires_at=self.now,
            payload={'engine':'SCALPER','server_name':'Demo','scalp_observation_id':row.pk})
        command_event(command,'COMMAND_CLAIM',now=self.now)
        command.state='EXECUTED';command.result={'broker_ticket':'42','detail':'FILLED'};command.save()
        command_event(command,'BROKER_ACK',now=self.now+timedelta(seconds=1))
        command_event(command,'BROKER_ACK',now=self.now+timedelta(seconds=2))
        self.assertEqual(ScalpAuditEvent.objects.filter(command=command).count(),2)
        self.assertEqual(ScalpAuditEvent.objects.get(kind='COMMAND_CLAIM').details['result'],{})

    def test_raw_signal_survives_paper_position_suppression(self):
        from .models import MarketBar,EAHeartbeat
        first=self.record()
        next_now=self.now+timedelta(minutes=1)
        b=MarketBar.objects.create(source='demo',symbol='GOLD',timeframe='M1',
            timestamp=first.signal_bar_time+timedelta(minutes=1),open=107,high=107.8,low=106.5,close=107.7)
        MarketBar.objects.filter(pk=b.pk).update(received_at=next_now-timedelta(seconds=1))
        self.feed.payload['quote_time']=next_now.isoformat();self.feed.save()
        EAHeartbeat.objects.filter(pk=self.feed.pk).update(last_seen=next_now)
        record_scalp(self.feed,now=next_now)
        second=ScalpObservation.objects.order_by('-recorded_at').first()
        self.assertEqual(second.reason,'PAPER_POSITION_UNSETTLED')
        self.assertNotEqual(second.snapshot['raw_signal']['reason'],'PAPER_POSITION_UNSETTLED')

    def test_scalp_costs_use_command_quote_without_a_main_decision(self):
        from .execution_observer import enrich_outcome
        command=EACommand.objects.create(command_id='scalp-costs',instance_id='demo',account_login='123',symbol='GOLD',
            action='OPEN',state='EXECUTED',expires_at=self.now,
            payload={'engine':'SCALPER','side':'BUY','market_snapshot':{'bid':100,'ask':100.2,'point':.01}},
            result={'execution':{'version':1,'request_price':100.22,'fill_price':100.3,'bid':100,'ask':100.2}})
        outcome={'trade_id':'test-unseen','entry_price':100.3}
        enrich_outcome(outcome,None,command)
        self.assertAlmostEqual(outcome['decision_to_fill_points'],10)
        self.assertAlmostEqual(outcome['request_slippage_points'],8)
