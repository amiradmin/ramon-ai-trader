import json
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from .models import EACommand


class EACommandEndpointTests(TestCase):
    headers = {"HTTP_AUTHORIZATION": "Bearer test-token"}
    next_url = "/api/ea/v1/commands/next"
    ack_url = "/api/ea/v1/commands/ack"

    def setUp(self):
        self.payload = {
            "command_id": "cmd-test-1",
            "instance_id": "ea-test-1",
            "account_login": "12345",
            "symbol": "XAUUSD_o",
            "action": EACommand.Action.OPEN,
            "payload": {
                "side": "SELL", "volume": "0.01",
                "stop_loss": "2401.0", "take_profit": "2398.0",
            },
            "decision_id": "decision-test-1",
            "risk_verdict_id": "risk-test-1",
            "execution_authorized": True,
            "expires_at": timezone.now() + timedelta(minutes=1),
        }

    def create_command(self, **overrides):
        values = dict(
            command_id="cmd-test-1",
            instance_id="ea-test-1",
            account_login="12345",
            symbol="XAUUSD_o",
            action=EACommand.Action.OPEN,
            payload={"side": "SELL", "volume": "0.01", "stop_loss": 2401.0, "take_profit": 2398.0},
            decision_id="decision-test-1",
            risk_verdict_id="risk-test-1",
            execution_authorized=True,
            expires_at=timezone.now() + timedelta(minutes=1),
        )
        values.update(overrides)
        return EACommand.objects.create(**values)

    @override_settings(EA_BEARER_TOKEN="test-token", RAMON_EA_COMMAND_EXECUTION_ENABLED=False)
    def test_poll_is_fail_closed_by_default(self):
        self.create_command()
        response = self.client.post(
            self.next_url,
            data=json.dumps({"instance_id": "ea-test-1", "account_login": "12345"}),
            content_type="application/json",
            **self.headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["commands_enabled"])
        self.assertFalse(response.json()["trading_authorization"])
        self.assertIsNone(response.json()["command"])
        self.assertEqual(EACommand.objects.get().state, EACommand.State.PENDING)

    @override_settings(EA_BEARER_TOKEN="test-token", RAMON_EA_COMMAND_EXECUTION_ENABLED=True)
    def test_poll_scopes_command_to_instance_and_account(self):
        self.create_command()
        response = self.client.post(
            self.next_url,
            data=json.dumps({"instance_id": "other-instance", "account_login": "12345"}),
            content_type="application/json",
            **self.headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["command"])
        self.assertEqual(EACommand.objects.get().state, EACommand.State.PENDING)

    @override_settings(EA_BEARER_TOKEN="test-token", RAMON_EA_COMMAND_EXECUTION_ENABLED=True)
    def test_poll_claims_only_authorized_unexpired_command(self):
        self.create_command(command_id="unauthorized", execution_authorized=False)
        self.create_command(
            command_id="expired", expires_at=timezone.now() - timedelta(seconds=1)
        )
        valid = self.create_command(command_id="valid")
        response = self.client.post(
            self.next_url,
            data=json.dumps({"instance_id": "ea-test-1", "account_login": "12345"}),
            content_type="application/json",
            **self.headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["command"]["command_id"], valid.command_id)
        self.assertTrue(response.json()["trading_authorization"])
        self.assertEqual(valid.__class__.objects.get(command_id="valid").state, EACommand.State.CLAIMED)
        self.assertEqual(valid.__class__.objects.get(command_id="expired").state, EACommand.State.EXPIRED)

    @override_settings(EA_BEARER_TOKEN="test-token", RAMON_EA_COMMAND_EXECUTION_ENABLED=True)
    def test_ack_is_account_scoped_and_terminal_ack_is_idempotent(self):
        self.create_command()
        wrong_scope = self.client.post(
            self.ack_url,
            data=json.dumps({
                "command_id": "cmd-test-1", "instance_id": "ea-test-1",
                "account_login": "99999", "state": "EXECUTED", "result": {"deal": "42"},
            }),
            content_type="application/json",
            **self.headers,
        )
        self.assertEqual(wrong_scope.status_code, 404)

        self.client.post(
            self.next_url,
            data=json.dumps({"instance_id": "ea-test-1", "account_login": "12345"}),
            content_type="application/json",
            **self.headers,
        )
        ack_payload = {
            "command_id": "cmd-test-1", "instance_id": "ea-test-1",
            "account_login": "12345", "state": "EXECUTED", "result": {"deal": "42"},
        }
        first = self.client.post(self.ack_url, data=json.dumps(ack_payload), content_type="application/json", **self.headers)
        second = self.client.post(self.ack_url, data=json.dumps(ack_payload), content_type="application/json", **self.headers)
        self.assertEqual(first.status_code, 200)
        self.assertFalse(first.json()["duplicate"])
        self.assertTrue(second.json()["duplicate"])

    @override_settings(EA_BEARER_TOKEN="test-token", RAMON_EA_COMMAND_EXECUTION_ENABLED=False)
    def test_missing_token_is_rejected(self):
        response = self.client.post(
            self.next_url,
            data=json.dumps({"instance_id": "ea-test-1", "account_login": "12345"}),
            content_type="application/json",
        )
        self.assertIn(response.status_code, (401, 503))
