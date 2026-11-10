from datetime import timedelta
from django.test import TestCase
from django.utils import timezone

from .models import EACommand,EAHeartbeat,LearningTradeOutcome,StrategyDecisionRecord
from .execution_observer import request_metrics,enrich_outcome


class ExecutionObserverTests(TestCase):
    def setUp(self):
        self.decision=StrategyDecisionRecord.objects.create(decision_id="execution-d",symbol="XAUUSD_o",action="BUY",
            confidence=.8,rationale_code="APPROVED",model_version="test",
            features={"market_snapshot":{"bid":100,"ask":100.2,"point":.01}})
        self.command=EACommand.objects.create(command_id="execution-c",instance_id="demo",account_login="123",
            symbol="XAUUSD_o",action="OPEN",state="EXECUTED",decision_id=self.decision.decision_id,
            payload={"side":"BUY"},expires_at=timezone.now()+timedelta(seconds=60),
            result={"execution":{"version":1,"request_price":100.3,"fill_price":100.25,"bid":100.1,"ask":100.3}})
        EAHeartbeat.objects.create(instance_id="demo",account_login="123",symbol="XAUUSD_o",payload={"account_trade_mode":0})

    def test_signed_request_slippage_and_decision_drift_are_different(self):
        values=request_metrics(self.command)
        self.assertAlmostEqual(values["request_slippage_points"],-5)
        self.assertAlmostEqual(values["submission_spread_points"],20)
        outcome={"trade_id":"new","entry_price":100.25}
        enrich_outcome(outcome,self.decision,self.command)
        self.assertAlmostEqual(outcome["decision_to_fill_points"],5)
        self.command.payload["side"]="SELL"
        self.assertAlmostEqual(request_metrics(self.command)["request_slippage_points"],5)

    def test_missing_or_partial_fill_does_not_become_zero_slippage(self):
        self.command.result={}
        self.assertEqual(request_metrics(self.command),{})
        self.command.state="RECONCILIATION_REQUIRED"
        self.assertEqual(request_metrics(self.command),{})

    def test_legacy_retry_keeps_its_old_normalized_schema(self):
        LearningTradeOutcome.objects.create(trade_id="legacy",realized_pnl=1,execution_metrics={"commission":-.5})
        outcome={"trade_id":"legacy","entry_price":100.25,"fee":-.1}
        enrich_outcome(outcome,self.decision,self.command)
        self.assertNotIn("entry_price",outcome)
        self.assertNotIn("request_slippage_points",outcome)
        self.assertNotIn("fee",outcome)

    def test_summary_is_account_scoped_and_missing_costs_stay_unavailable(self):
        LearningTradeOutcome.objects.create(trade_id="ours",instance_id="demo",account_login="123",symbol="XAUUSD_o",
            realized_pnl=2,closed_at=timezone.now(),execution_metrics={"commission":-.5,"swap":0})
        LearningTradeOutcome.objects.create(trade_id="other",instance_id="other",account_login="123",symbol="XAUUSD_o",
            realized_pnl=200,closed_at=timezone.now(),execution_metrics={"commission":-99})
        data=self.client.get("/api/ea/v1/research/execution").json()
        self.assertEqual(data["closed_trades"],1)
        self.assertEqual(data["metrics"]["commission"]["average"],-.5)
        self.assertIsNone(data["metrics"]["decision_to_fill_points"]["average"])
        self.assertEqual(data["metrics"]["swap"]["average"],0)
        self.assertAlmostEqual(data["metrics"]["request_slippage_points"]["average"],-5)
        self.assertFalse(data["affects_orders"])
