import io
import json
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from .strategy_evidence import checksum, quality_issues
from .models import DecisionEvaluation, RiskVerdictRecord


class StrategyEvidenceTests(TestCase):
    def evidence(self):
        now = timezone.now()
        inputs = dict(bars=[dict(time=int(now.timestamp())-901, closed=True)],
                      received_at=[now.isoformat()], telemetry_received_at=now.isoformat())
        return now, dict(schema_version=1, inputs=inputs, input_sha256=checksum(inputs),
                         captured_at=now.isoformat(), source_sha256={'test': 'hash'})

    def test_receipt_and_bar_causality_and_checksum(self):
        now, evidence = self.evidence()
        self.assertEqual(quality_issues(evidence, now), [])
        evidence['inputs']['received_at'] = [(now+timedelta(seconds=1)).isoformat()]
        evidence['inputs']['bars'][0]['time'] = int(now.timestamp())
        issues = quality_issues(evidence, now)
        self.assertIn('INPUT_CHECKSUM_MISMATCH', issues)
        self.assertIn('INPUT_RECEIPT_INVALID_OR_LATE', issues)
        self.assertIn('FORMING_OR_FUTURE_BAR', issues)

    def test_export_excludes_other_scopes_and_marks_legacy_ineligible(self):
        verdict = RiskVerdictRecord.objects.create(decision_id='test', approved=False, reason_code='WAIT')
        DecisionEvaluation.objects.create(decision_id='test', instance_id='demo', account_login='1',
            server_name='Demo', symbol='XAUUSD', action='WAIT', rationale_code='WAIT', confidence=0,
            model_version='test', features={'strategy_setup': 'TREND_PULLBACK'}, verdict=verdict)
        with TemporaryDirectory() as directory:
            path = Path(directory)/'journal.jsonl'
            args = dict(source='demo', account_login='1', server='Demo', symbol='XAUUSD',
                        setup='TREND_PULLBACK', output=str(path), stdout=io.StringIO())
            call_command('export_strategy_journal', **args)
            records = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(records[-1]['evaluations'], 1)
            self.assertFalse(records[1]['broker_training_eligible'])
            self.assertIn('LEGACY_INPUT_EVIDENCE_MISSING', records[1]['quality_issues'])
            args.update(server='Other', output=str(Path(directory)/'other.jsonl'))
            call_command('export_strategy_journal', **args)
            self.assertEqual(json.loads(Path(args['output']).read_text().splitlines()[-1])['evaluations'], 0)

    def test_export_requires_exact_command_and_respects_label_receipt_cutoff(self):
        from .models import EACommand, LearningTradeOutcome
        now, evidence = self.evidence()
        verdict = RiskVerdictRecord.objects.create(decision_id='label-test', approved=True, reason_code='OK')
        evaluation = DecisionEvaluation.objects.create(decision_id='label-test', instance_id='demo',
            account_login='1', server_name='Demo', symbol='XAUUSD', action='BUY', rationale_code='OK',
            confidence=.8, model_version='test', verdict=verdict,
            features={'strategy_setup': 'TREND_PULLBACK', 'input_evidence': evidence})
        command = EACommand.objects.create(command_id='exact-command', instance_id='demo', account_login='1',
            symbol='XAUUSD', action='OPEN', decision_id='label-test', evaluation=evaluation,
            expires_at=now+timedelta(seconds=30), payload={'server_name': 'Demo'})
        cutoff = timezone.now()
        outcome = LearningTradeOutcome.objects.create(trade_id='1:Demo:ticket', decision_id='label-test',
            instance_id='demo', account_login='1', symbol='XAUUSD', realized_pnl=2,
            closed_at=now, tags={'command_id': command.command_id})
        with TemporaryDirectory() as directory:
            def export(name, as_of):
                path = Path(directory)/name
                call_command('export_strategy_journal', source='demo', account_login='1', server='Demo',
                    symbol='XAUUSD', setup='TREND_PULLBACK', output=str(path),
                    as_of=as_of.isoformat(), stdout=io.StringIO())
                return json.loads(path.read_text().splitlines()[1])
            self.assertFalse(export('before.jsonl', cutoff)['broker_training_eligible'])
            self.assertTrue(export('after.jsonl', timezone.now())['broker_training_eligible'])
            outcome.tags={'command_id': 'wrong-command'}
            outcome.save()
            self.assertFalse(export('wrong.jsonl', timezone.now())['broker_training_eligible'])
