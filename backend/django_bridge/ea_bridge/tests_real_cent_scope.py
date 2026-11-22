"""Execution fails closed unless the exact broker-reported cent account matches."""
from datetime import timedelta
import json

from django.test import TestCase, override_settings
from django.utils import timezone

from .account_scope import real_cent_scope
from .models import EACommand, EAHeartbeat


SCOPE = dict(
    RAMON_REAL_CENT_INSTANCE="bridge-test", RAMON_REAL_CENT_LOGIN="12345",
    RAMON_REAL_CENT_SERVER="Broker-Live", RAMON_REAL_CENT_CURRENCY="USC",
    RAMON_REAL_CENT_SYMBOL="XAUUSD_i", EA_BEARER_TOKEN="test-token",
    RAMON_EA_COMMAND_EXECUTION_ENABLED=True,
)


@override_settings(**SCOPE)
class RealCentScopeTests(TestCase):
    def setUp(self):
        self.feed = EAHeartbeat.objects.create(
            instance_id="bridge-test", account_login="12345", server_name="Broker-Live",
            symbol="XAUUSD_i", status="TELEMETRY_OK", terminal_connected=True,
            trade_allowed=True, payload={"account_trade_mode": 2, "server_name": "Broker-Live",
                                         "account": {"currency": "USC"}},
        )

    def poll(self):
        return self.client.post(
            "/api/ea/v1/commands/next", data=json.dumps({
                "instance_id": "bridge-test", "account_login": "12345"}),
            content_type="application/json", HTTP_AUTHORIZATION="Bearer test-token",
        ).json()

    def test_only_exact_real_cent_scope_can_claim_pending_order(self):
        command = EACommand.objects.create(
            command_id="pending-cent-test", instance_id="bridge-test", account_login="12345",
            symbol="XAUUSD_i", action=EACommand.Action.OPEN,
            payload={"server_name": "Broker-Live"}, execution_authorized=True,
            expires_at=timezone.now() + timedelta(minutes=1),
        )
        self.assertTrue(real_cent_scope(self.feed)[0])
        self.assertEqual(self.poll()["command"]["command_id"], command.command_id)
        command.state = EACommand.State.PENDING
        command.claimed_at = None
        command.save(update_fields=["state", "claimed_at"])
        for changed, value, expected in (
            ("account_trade_mode", 0, "REAL_ACCOUNT_REQUIRED"),
            ("account_trade_mode", "2", "REAL_ACCOUNT_REQUIRED"),
            ("server_name", "Other-Live", "ACCOUNT_SERVER_MISMATCH"),
        ):
            snapshot = dict(self.feed.payload)
            snapshot[changed] = value
            self.feed.payload = snapshot
            self.feed.save(update_fields=["payload"])
            self.assertEqual(self.poll()["authorization_reason"], expected)
            command.refresh_from_db()
            self.assertEqual(command.state, EACommand.State.PENDING)
        snapshot = dict(self.feed.payload)
        snapshot.update(account_trade_mode=2, server_name="Broker-Live", account={"currency": "USD"})
        self.feed.payload = snapshot
        self.feed.save(update_fields=["payload"])
        self.assertEqual(self.poll()["authorization_reason"], "REAL_CENT_CURRENCY_MISMATCH")

    @override_settings(RAMON_REAL_CENT_CURRENCY="")
    def test_missing_currency_configuration_cannot_authorize(self):
        self.assertEqual(real_cent_scope(self.feed), (False, "REAL_CENT_SCOPE_NOT_CONFIGURED"))
        self.assertEqual(self.poll()["authorization_reason"], "REAL_CENT_SCOPE_NOT_CONFIGURED")

    def test_other_account_or_symbol_is_not_the_configured_scope(self):
        self.feed.symbol = "XAUUSD_o"
        self.feed.save(update_fields=["symbol"])
        self.assertEqual(real_cent_scope(self.feed), (False, "REAL_CENT_SCOPE_MISMATCH"))
