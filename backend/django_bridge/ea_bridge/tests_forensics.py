"""Read-only forensic analytics: strict attribution, missing data and range bounds."""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from .models import DecisionEvaluation, EACommand, LearningTradeOutcome, RiskVerdictRecord


class TradeForensicsTests(TestCase):
    url = "/api/ea/v1/learning/forensics/data"

    def outcome(self, trade_id, pnl, *, command_id=None, instance="ea-1",
                account="demo-1", symbol="XAUUSD_o", hours_ago=2):
        return LearningTradeOutcome.objects.create(
            trade_id=trade_id, decision_id="decision-1",
            instance_id=instance, account_login=account, symbol=symbol,
            realized_pnl=Decimal(str(pnl)), closed_at=timezone.now()-timedelta(hours=hours_ago),
            tags={"command_id": command_id, "regime": "BEARISH", "side": "SELL"}
                 if command_id else {"regime": "BEARISH", "side": "SELL"},
            decision_snapshot={"action": "SELL"},
        )

    def command(self):
        verdict = RiskVerdictRecord.objects.create(
            decision_id="decision-1", approved=True,
            reason_code="APPROVED", approved_volume=Decimal("0.01"))
        evaluation = DecisionEvaluation.objects.create(
            decision_id="decision-1", instance_id="ea-1", account_login="demo-1",
            server_name="demo-server", symbol="XAUUSD_o", action="SELL",
            rationale_code="SIGNALFLOW", confidence=.72, model_version="test",
            features={"stages": {"opportunities": {
                "regime": {"label": "BEARISH", "adx": 21, "ema_bias": "BEAR"},
                "setup": {"type": "TREND_PULLBACK"}}, "entry_timing": {"chase_atr": 0.42, "quality": 0.7, "signal_age_bars": 1}}}, verdict=verdict)
        return EACommand.objects.create(
            command_id="open-test-1", instance_id="ea-1", account_login="demo-1",
            symbol="XAUUSD_o", action="OPEN", state="EXECUTED",
            decision_id="decision-1", evaluation=evaluation,
            expires_at=timezone.now()+timedelta(minutes=1),
            payload={"side": "SELL", "strategy_setup": "TREND_PULLBACK"})

    def test_empty_interval_is_safe(self):
        response=self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["summary"]["trades"], 0)

    def test_verified_link_and_aggregation(self):
        self.command()
        self.outcome("one", -10, command_id="open-test-1", hours_ago=2)
        self.outcome("two", 15, command_id="open-test-1", hours_ago=1)
        data=self.client.get(self.url).json()
        self.assertTrue(data["read_only"])
        self.assertFalse(data["auto_apply"])
        self.assertEqual(data["summary"]["trades"],2)
        self.assertEqual(data["summary"]["net_pnl"],5)
        self.assertEqual(data["summary"]["win_rate"],50)
        self.assertEqual(data["summary"]["linked_evaluations"],2)
        self.assertEqual(data["by_strategy"][0]["name"],"TREND_PULLBACK")
        self.assertEqual(data["trades"][0]["adx"],21)
        self.assertEqual(data["trades"][0]["chase_atr"],0.42)
        self.assertEqual(data["trades"][0]["entry_quality"],0.7)
        self.assertEqual(data["sell_research"]["summary"]["all_sell"]["trades"],2)
        self.assertEqual(data["sell_research"]["summary"]["sell_winners"]["trades"],1)
        self.assertEqual(data["sell_research"]["summary"]["sell_losers"]["trades"],1)

    def test_unverified_account_never_receives_other_accounts_evidence(self):
        self.command()
        self.outcome("wrong-account",-10,command_id="open-test-1",account="demo-2")
        data=self.client.get(self.url).json()
        row=data["trades"][0]
        self.assertEqual(row["evidence_status"],"UNLINKED")
        self.assertIsNone(row["evaluation_id"])
        self.assertIn("COMMAND_LINK_UNVERIFIED",row["missing_evidence"])

    def test_unlinked_outcome_is_not_guess_attributed(self):
        self.command()
        self.outcome("unlinked", -3)
        row=self.client.get(self.url).json()["trades"][0]
        self.assertEqual(row["evidence_status"],"UNLINKED")
        self.assertIsNone(row["confidence"])
        self.assertEqual(row["regime"],"BEARISH")

    def test_date_range_and_limits_are_validated(self):
        self.assertEqual(self.client.get(self.url,{"limit":"501"}).status_code,400)
        self.assertEqual(self.client.get(self.url,{"start":"2026-01-01","end":"2026-08-01"}).status_code,400)
        self.assertEqual(self.client.get(self.url,{"start":"2026-11-02T10:00:00"}).status_code,400)

    def test_symbol_and_account_filters(self):
        self.outcome("gold", -10)
        self.outcome("eur", 5, symbol="EURUSD", account="demo-2")
        data=self.client.get(self.url,{"symbol":"XAUUSD_o","account_login":"demo-1"}).json()
        self.assertEqual(data["summary"]["trades"],1)
        self.assertEqual(data["summary"]["net_pnl"],-10)

    def test_page_is_available(self):
        response=self.client.get("/api/ea/v1/learning/forensics")
        self.assertEqual(response.status_code,200)
        self.assertContains(response,"Trade Forensic Analyzer")
