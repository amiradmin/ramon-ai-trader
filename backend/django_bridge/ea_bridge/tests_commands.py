import json
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone

from ramon_pipeline.signalflow.risk_management import RiskConfig

from .command_producer import _is_demo_account
from .models import (
    AccountDailyBaseline,
    EACommand,
    EAHeartbeat,
    RiskVerdictRecord,
    StrategyDecisionRecord,
)


class DemoAccountGateTests(TestCase):
    def test_only_explicit_integer_demo_mode_is_accepted(self):
        self.assertTrue(_is_demo_account({"account_trade_mode": 0}))
        self.assertFalse(_is_demo_account({}))
        self.assertFalse(_is_demo_account({"account_trade_mode": None}))
        self.assertFalse(_is_demo_account({"account_trade_mode": 2}))
        self.assertFalse(_is_demo_account({"account_trade_mode": False}))
        self.assertFalse(_is_demo_account(None))


@override_settings(
    EA_BEARER_TOKEN="test-token",
    RAMON_EA_COMMAND_CREATION_ENABLED=True,
    RAMON_EA_COMMAND_EXECUTION_ENABLED=True,
)
class EACommandDeliveryHardeningTests(TestCase):
    headers = {"HTTP_AUTHORIZATION": "Bearer test-token"}
    next_url = "/api/ea/v1/commands/next"
    ack_url = "/api/ea/v1/commands/ack"

    def setUp(self):
        self.now = timezone.now()
        self.broker_day = self.now.date()
        self.instance_id = "ea-delivery-test"
        self.account_login = "12345"
        self.server_name = "Demo-Server"
        self.symbol = "XAUUSD_o"
        self.bid = Decimal("2400.0")
        self.ask = Decimal("2400.2")
        self.balance = Decimal("10000.00")
        self.equity = Decimal("10000.00")
        self.decision_id = "decision-delivery-test"
        self.heartbeat_payload = {
            "server_name": self.server_name,
            "account_trade_mode": 0,
            "broker_day": self.broker_day.isoformat(),
            "quote_time": self.now.isoformat(),
            "quote": {"bid": str(self.bid), "ask": str(self.ask), "point": "0.01"},
            "account": {"balance": str(self.balance), "equity": str(self.equity)},
            "positions": [],
        }
        self.heartbeat = EAHeartbeat.objects.create(
            instance_id=self.instance_id,
            account_login=self.account_login,
            server_name=self.server_name,
            symbol=self.symbol,
            status="TELEMETRY_OK",
            terminal_connected=True,
            trade_allowed=True,
            payload=self.heartbeat_payload,
        )
        # Deliberately use another source instance: baseline identity is account,
        # server and broker day; source_instance_id is provenance, not ownership.
        self.baseline = AccountDailyBaseline.objects.create(
            account_login=self.account_login,
            server_name=self.server_name,
            broker_day=self.broker_day,
            opening_balance="10000.00",
            source_instance_id="previous-ea-instance",
            capture_method="BROKER_HISTORY_DERIVED",
        )
        self.create_open_command()

    def create_open_command(self, *, equity=None, decision_action="SELL", verdict_approved=True,
                            command_payload=None, command_id="cmd-delivery-test"):
        equity = Decimal(str(equity if equity is not None else self.equity))
        decision = StrategyDecisionRecord.objects.create(
            decision_id=self.decision_id,
            symbol=self.symbol,
            action=decision_action,
            confidence=0.9,
            rationale_code="TEST",
            model_version="test-model",
            features={
                "account_snapshot": {
                    "balance": str(self.balance),
                    "equity": str(equity),
                }
            },
        )
        verdict = RiskVerdictRecord.objects.create(
            decision_id=decision.decision_id,
            approved=verdict_approved,
            reason_code="APPROVED" if verdict_approved else "NOT_APPROVED",
            approved_volume="0.01000000",
            stop_loss="2401.0000000000" if decision_action == "SELL" else "2399.0000000000",
            take_profit="2398.0000000000" if decision_action == "SELL" else "2402.0000000000",
            details={"daily_baseline_day": self.broker_day.isoformat()},
        )
        payload = command_payload or {
            "side": decision_action,
            "volume": "0.01",
            "stop_loss": str(verdict.stop_loss),
            "take_profit": str(verdict.take_profit),
        }
        return EACommand.objects.create(
            command_id=command_id,
            instance_id=self.instance_id,
            account_login=self.account_login,
            symbol=self.symbol,
            action=EACommand.Action.OPEN,
            payload=payload,
            decision_id=decision.decision_id,
            risk_verdict_id=str(verdict.pk),
            execution_authorized=True,
            expires_at=timezone.now() + timedelta(minutes=1),
        )

    def poll(self, **overrides):
        # v1.04 compatibility: server_name and symbol may be omitted. The backend
        # derives them from the fresh, authenticated heartbeat and checks command scope.
        payload = {
            "instance_id": self.instance_id,
            "account_login": self.account_login,
        }
        payload.update(overrides)
        return self.client.post(
            self.next_url,
            data=json.dumps(payload),
            content_type="application/json",
            **self.headers,
        )

    def test_fresh_demo_account_claims_valid_command_without_new_ea_poll_fields(self):
        response = self.poll()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["trading_authorization"])
        self.assertEqual(response.json()["command"]["command_id"], "cmd-delivery-test")
        self.assertEqual(
            EACommand.objects.get(command_id="cmd-delivery-test").state,
            EACommand.State.CLAIMED,
        )

    def test_optional_server_and_symbol_are_checked_when_supplied(self):
        command = EACommand.objects.get(command_id="cmd-delivery-test")
        response = self.poll(server_name="Wrong-Server")
        self.assertIsNone(response.json()["command"])
        command.refresh_from_db()
        self.assertEqual(command.result["reason"], "ACCOUNT_SERVER_MISMATCH")

    def test_request_symbol_must_match_heartbeat_and_command(self):
        command = EACommand.objects.get(command_id="cmd-delivery-test")
        response = self.poll(symbol="EURUSD")
        self.assertIsNone(response.json()["command"])
        command.refresh_from_db()
        self.assertEqual(command.result["reason"], "ACCOUNT_SYMBOL_MISMATCH")

    def test_stale_heartbeat_rejects_command(self):
        EAHeartbeat.objects.filter(pk=self.heartbeat.pk).update(
            last_seen=timezone.now() - timedelta(seconds=120)
        )
        command = EACommand.objects.get(command_id="cmd-delivery-test")
        response = self.poll()
        self.assertIsNone(response.json()["command"])
        command.refresh_from_db()
        self.assertEqual(command.result["reason"], "ACCOUNT_TELEMETRY_STALE")

    def test_live_account_is_rejected(self):
        self.heartbeat_payload["account_trade_mode"] = 2
        self.heartbeat.payload = self.heartbeat_payload
        self.heartbeat.save(update_fields=["payload"])
        command = EACommand.objects.get(command_id="cmd-delivery-test")
        self.poll()
        command.refresh_from_db()
        self.assertEqual(command.result["reason"], "DEMO_ACCOUNT_REQUIRED")

    def test_open_command_is_rejected_if_any_position_exists(self):
        self.heartbeat_payload["positions"] = [{"ticket": "77"}]
        self.heartbeat.payload = self.heartbeat_payload
        self.heartbeat.save(update_fields=["payload"])
        command = EACommand.objects.get(command_id="cmd-delivery-test")
        self.poll()
        command.refresh_from_db()
        self.assertEqual(command.result["reason"], "OPEN_POSITION_RISK_UNVERIFIED")

    def test_stale_quote_is_rejected(self):
        self.heartbeat_payload["quote_time"] = (
            timezone.now() - timedelta(seconds=90)
        ).isoformat()
        self.heartbeat.payload = self.heartbeat_payload
        self.heartbeat.save(update_fields=["payload"])
        command = EACommand.objects.get(command_id="cmd-delivery-test")
        self.poll()
        command.refresh_from_db()
        self.assertEqual(command.result["reason"], "QUOTE_STALE")

    def test_missing_daily_baseline_is_rejected(self):
        self.baseline.delete()
        command = EACommand.objects.get(command_id="cmd-delivery-test")
        self.poll()
        command.refresh_from_db()
        self.assertEqual(command.result["reason"], "DAILY_LOSS_BASELINE_UNAVAILABLE")

    def test_daily_loss_limit_is_rechecked_at_delivery(self):
        threshold = Decimal(str(RiskConfig().max_daily_loss_percent))
        equity = Decimal(str(self.baseline.opening_balance)) * (Decimal("1") - threshold / Decimal("100"))
        self.heartbeat_payload["account"]["equity"] = str(equity)
        self.heartbeat.payload = self.heartbeat_payload
        self.heartbeat.save(update_fields=["payload"])
        command = EACommand.objects.get(command_id="cmd-delivery-test")
        decision = StrategyDecisionRecord.objects.get(decision_id=command.decision_id)
        decision.features["account_snapshot"]["equity"] = str(equity)
        decision.save(update_fields=["features"])
        self.poll()
        command.refresh_from_db()
        self.assertEqual(command.result["reason"], "DAILY_LOSS_LIMIT_REACHED")

    def test_account_equity_change_after_decision_is_rejected(self):
        self.heartbeat_payload["account"]["equity"] = "9990.00"
        self.heartbeat.payload = self.heartbeat_payload
        self.heartbeat.save(update_fields=["payload"])
        command = EACommand.objects.get(command_id="cmd-delivery-test")
        self.poll()
        command.refresh_from_db()
        self.assertEqual(command.result["reason"], "ACCOUNT_EQUITY_CHANGED_AFTER_DECISION")

    def test_command_payload_must_match_approved_risk_verdict(self):
        command = EACommand.objects.get(command_id="cmd-delivery-test")
        command.payload = {
            "side": "SELL",
            "volume": "0.10",
            "stop_loss": "2401.0",
            "take_profit": "2398.0",
        }
        command.save(update_fields=["payload"])
        self.poll()
        command.refresh_from_db()
        self.assertEqual(command.result["reason"], "COMMAND_VOLUME_DOES_NOT_MATCH_RISK_VERDICT")

    def test_unapproved_risk_verdict_is_rejected(self):
        command = EACommand.objects.get(command_id="cmd-delivery-test")
        verdict = RiskVerdictRecord.objects.get(pk=command.risk_verdict_id)
        verdict.approved = False
        verdict.save(update_fields=["approved"])
        self.poll()
        command.refresh_from_db()
        self.assertEqual(command.result["reason"], "APPROVED_RISK_VERDICT_UNAVAILABLE")

    def test_disabled_creation_gate_blocks_delivery_and_preserves_pending_command(self):
        command = EACommand.objects.get(command_id="cmd-delivery-test")
        with self.settings(RAMON_EA_COMMAND_CREATION_ENABLED=False):
            response = self.poll()
        self.assertFalse(response.json()["commands_enabled"])
        self.assertFalse(response.json()["trading_authorization"])
        command.refresh_from_db()
        self.assertEqual(command.state, EACommand.State.PENDING)

    def test_expired_command_is_expired_before_delivery(self):
        command = EACommand.objects.get(command_id="cmd-delivery-test")
        EACommand.objects.filter(pk=command.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        response = self.poll()
        self.assertIsNone(response.json()["command"])
        command.refresh_from_db()
        self.assertEqual(command.state, EACommand.State.EXPIRED)
        self.assertEqual(command.result["reason"], "COMMAND_EXPIRED_BEFORE_DELIVERY")

    def test_acknowledgement_is_account_scoped_and_terminal_ack_is_idempotent(self):
        command = EACommand.objects.get(command_id="cmd-delivery-test")
        command.state = EACommand.State.CLAIMED
        command.save(update_fields=["state"])
        wrong_scope = self.client.post(
            self.ack_url,
            data=json.dumps({
                "command_id": command.command_id,
                "instance_id": self.instance_id,
                "account_login": "99999",
                "state": "EXECUTED",
                "result": {"deal": "42"},
            }),
            content_type="application/json",
            **self.headers,
        )
        self.assertEqual(wrong_scope.status_code, 404)
        ack_payload = {
            "command_id": command.command_id,
            "instance_id": self.instance_id,
            "account_login": self.account_login,
            "state": "EXECUTED",
            "result": {"deal": "42"},
        }
        first = self.client.post(
            self.ack_url, data=json.dumps(ack_payload),
            content_type="application/json", **self.headers,
        )
        second = self.client.post(
            self.ack_url, data=json.dumps(ack_payload),
            content_type="application/json", **self.headers,
        )
        self.assertEqual(first.status_code, 200)
        self.assertFalse(first.json()["duplicate"])
        self.assertTrue(second.json()["duplicate"])

    def test_missing_bearer_token_is_rejected(self):
        response = self.client.post(
            self.next_url,
            data=json.dumps({"instance_id": self.instance_id, "account_login": self.account_login}),
            content_type="application/json",
        )
        self.assertIn(response.status_code, (401, 503))
