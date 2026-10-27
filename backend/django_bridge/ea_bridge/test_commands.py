import json
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from .models import EACommand


class EACommandApiTests(TestCase):
    next_url = "/api/ea/v1/commands/next"
    ack_url = "/api/ea/v1/commands/ack"
    headers = {"HTTP_AUTHORIZATION": "Bearer test-token"}

    def setUp(self):
        self.scope = {"instance_id": "ramon-test-instance", "account_login": "123456"}

    def post_json(self, url, payload, headers=None):
        return self.client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
            **(headers or self.headers),
        )

    @override_settings(EA_BEARER_TOKEN="test-token", RAMON_EA_COMMAND_EXECUTION_ENABLED=False)
    def test_command_polling_is_disabled_by_default(self):
        EACommand.objects.create(
            command_id="cmd-disabled",
            instance_id=self.scope["instance_id"],
            account_login=self.scope["account_login"],
            symbol="XAUUSD_o",
            action=EACommand.Action.OPEN,
            payload={"direction": "BUY", "volume": "0.01", "stop_loss": 2300, "take_profit": 2310},
            decision_id="decision-test",
            risk_verdict_id="risk-test",
            execution_authorized=True,
            expires_at=timezone.now() + timedelta(minutes=1),
        )
        response = self.post_json(self.next_url, self.scope)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["commands_enabled"])
        self.assertFalse(response.json()["trading_authorization"])
        self.assertIsNone(response.json()["command"])
        self.assertEqual(EACommand.objects.get(command_id="cmd-disabled").state, EACommand.State.PENDING)

    @override_settings(EA_BEARER_TOKEN="test-token", RAMON_EA_COMMAND_EXECUTION_ENABLED=True)
    def test_poll_claims_only_authorized_unexpired_command_for_exact_account(self):
        now = timezone.now()
        command = EACommand.objects.create(
            command_id="cmd-ready",
            instance_id=self.scope["instance_id"],
            account_login=self.scope["account_login"],
            symbol="XAUUSD_o",
            action=EACommand.Action.OPEN,
            payload={"direction": "BUY", "volume": "0.01", "stop_loss": 2300, "take_profit": 2310},
            decision_id="decision-test",
            risk_verdict_id="risk-test",
            execution_authorized=True,
            expires_at=now + timedelta(minutes=1),
        )
        EACommand.objects.create(
            command_id="cmd-other-account",
            instance_id=self.scope["instance_id"],
            account_login="different-account",
            symbol="XAUUSD_o",
            action=EACommand.Action.OPEN,
            payload={"direction": "BUY", "volume": "0.01", "stop_loss": 2300, "take_profit": 2310},
            execution_authorized=True,
            expires_at=now + timedelta(minutes=1),
        )
        EACommand.objects.create(
            command_id="cmd-not-authorized",
            instance_id=self.scope["instance_id"],
            account_login=self.scope["account_login"],
            symbol="XAUUSD_o",
            action=EACommand.Action.OPEN,
            payload={},
            execution_authorized=False,
            expires_at=now + timedelta(minutes=1),
        )
        response = self.post_json(self.next_url, self.scope)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["trading_authorization"])
        self.assertEqual(response.json()["command"]["command_id"], command.command_id)
        command.refresh_from_db()
        self.assertEqual(command.state, EACommand.State.CLAIMED)
        self.assertIsNotNone(command.claimed_at)

    @override_settings(EA_BEARER_TOKEN="test-token", RAMON_EA_COMMAND_EXECUTION_ENABLED=True)
    def test_expired_pending_command_is_marked_expired(self):
        command = EACommand.objects.create(
            command_id="cmd-expired",
            instance_id=self.scope["instance_id"],
            account_login=self.scope["account_login"],
            symbol="XAUUSD_o",
            action=EACommand.Action.NOOP,
            payload={},
            execution_authorized=True,
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        response = self.post_json(self.next_url, self.scope)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["command"])
        command.refresh_from_db()
        self.assertEqual(command.state, EACommand.State.EXPIRED)

    @override_settings(EA_BEARER_TOKEN="test-token", RAMON_EA_COMMAND_EXECUTION_ENABLED=True)
    def test_ack_is_scoped_and_terminal_duplicate_is_idempotent(self):
        command = EACommand.objects.create(
            command_id="cmd-ack",
            instance_id=self.scope["instance_id"],
            account_login=self.scope["account_login"],
            symbol="XAUUSD_o",
            action=EACommand.Action.NOOP,
            payload={},
            execution_authorized=False,
            state=EACommand.State.CLAIMED,
            expires_at=timezone.now() + timedelta(minutes=1),
        )
        ack = dict(self.scope, command_id=command.command_id, state="EXECUTED",
                   result={"broker_deal": "deal-123", "confirmed": True})
        response = self.post_json(self.ack_url, ack)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["state"], "EXECUTED")
        response = self.post_json(self.ack_url, ack)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["duplicate"])

    @override_settings(EA_BEARER_TOKEN="test-token", RAMON_EA_COMMAND_EXECUTION_ENABLED=True)
    def test_ack_cannot_access_command_for_another_account(self):
        EACommand.objects.create(
            command_id="cmd-private",
            instance_id=self.scope["instance_id"],
            account_login="actual-account",
            symbol="XAUUSD_o",
            action=EACommand.Action.NOOP,
            payload={},
            expires_at=timezone.now() + timedelta(minutes=1),
        )
        response = self.post_json(self.ack_url, dict(
            self.scope, command_id="cmd-private", state="EXECUTED", result={}
        ))
        self.assertEqual(response.status_code, 404)

    @override_settings(EA_BEARER_TOKEN="test-token", RAMON_EA_COMMAND_EXECUTION_ENABLED=True)
    def test_rejects_invalid_transition_from_claimed_to_pending(self):
        EACommand.objects.create(
            command_id="cmd-transition",
            instance_id=self.scope["instance_id"],
            account_login=self.scope["account_login"],
            symbol="XAUUSD_o",
            action=EACommand.Action.NOOP,
            payload={},
            state=EACommand.State.CLAIMED,
            expires_at=timezone.now() + timedelta(minutes=1),
        )
        response = self.post_json(self.ack_url, dict(
            self.scope, command_id="cmd-transition", state="PENDING", result={}
        ))
        self.assertEqual(response.status_code, 400)

    @override_settings(EA_BEARER_TOKEN="wrong-token", RAMON_EA_COMMAND_EXECUTION_ENABLED=True)
    def test_rejects_unauthenticated_poll(self):
        response = self.post_json(self.next_url, self.scope)
        self.assertEqual(response.status_code, 401)
