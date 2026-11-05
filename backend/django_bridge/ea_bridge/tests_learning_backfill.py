from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from ea_bridge.models import (
    EACommand,
    LearningTradeOutcome,
    RiskVerdictRecord,
    StrategyDecisionRecord,
)


class LearningRiskBackfillTests(TestCase):
    def setUp(self):
        self.decision = StrategyDecisionRecord.objects.create(
            decision_id="backfill-decision-001",
            symbol="XAUUSD_o",
            action="BUY",
            confidence=0.8,
            rationale_code="SIGNALFLOW_APPROVED",
            model_version="test-v1",
        )
        self.verdict = RiskVerdictRecord.objects.create(
            decision_id=self.decision.decision_id,
            approved=True,
            reason_code="RISK_APPROVED",
            approved_volume="0.01",
            details={"risk_stage": {"estimated_risk": "12.5", "volume": "0.01"}},
        )
        EACommand.objects.create(
            command_id="backfill-open-001",
            instance_id="ramon-command-ea-1",
            account_login="123456",
            symbol="XAUUSD_o",
            action=EACommand.Action.OPEN,
            payload={"side": "BUY", "volume": "0.01"},
            decision_id=self.decision.decision_id,
            risk_verdict_id=str(self.verdict.pk),
            execution_authorized=True,
            state=EACommand.State.EXECUTED,
            expires_at=timezone.now() + timedelta(minutes=1),
            result={"broker_ticket": "backfill-ticket-001"},
        )
        self.outcome = LearningTradeOutcome.objects.create(
            trade_id="123456:backfill-position-001",
            decision_id=self.decision.decision_id,
            instance_id="ramon-command-ea-1",
            account_login="123456",
            symbol="XAUUSD_o",
            realized_pnl=Decimal("6.25"),
            risk_amount=Decimal("0"),
            r_multiple=None,
            opened_at=timezone.now() - timedelta(hours=1),
            closed_at=timezone.now(),
        )

    def test_dry_run_does_not_mutate_outcome(self):
        output = StringIO()
        call_command("backfill_learning_risk_labels", stdout=output)
        self.outcome.refresh_from_db()
        self.assertIsNone(self.outcome.r_multiple)
        self.assertEqual(self.outcome.risk_amount, Decimal("0"))
        self.assertIn("eligible=1", output.getvalue())
        self.assertIn("Dry-run only", output.getvalue())

    def test_apply_uses_only_approved_command_risk(self):
        output = StringIO()
        call_command("backfill_learning_risk_labels", "--apply", stdout=output)
        self.outcome.refresh_from_db()
        self.assertEqual(self.outcome.risk_amount, Decimal("12.5"))
        self.assertEqual(self.outcome.r_multiple, Decimal("0.50000000"))
        self.assertIn("updated=1", output.getvalue())

    def test_ambiguous_command_match_is_skipped(self):
        EACommand.objects.create(
            command_id="backfill-open-ambiguous",
            instance_id="ramon-command-ea-1",
            account_login="123456",
            symbol="XAUUSD_o",
            action=EACommand.Action.OPEN,
            payload={"side": "BUY", "volume": "0.01"},
            decision_id=self.decision.decision_id,
            risk_verdict_id=str(self.verdict.pk),
            execution_authorized=True,
            state=EACommand.State.EXECUTED,
            expires_at=timezone.now() + timedelta(minutes=1),
            result={"broker_ticket": "backfill-ticket-002"},
        )
        output = StringIO()
        call_command("backfill_learning_risk_labels", "--apply", stdout=output)
        self.outcome.refresh_from_db()
        self.assertIsNone(self.outcome.r_multiple)
        self.assertEqual(self.outcome.risk_amount, Decimal("0"))
        self.assertIn("no unique executed OPEN command", output.getvalue())
