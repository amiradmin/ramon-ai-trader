"""Regression tests for the account-scoped OPEN loss circuit breaker."""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone

from .models import LearningTradeOutcome
from .trading_loss_guard import open_loss_guard_reason


@override_settings(RAMON_OPEN_LOSS_GUARD_ENABLED=True,
                   RAMON_OPEN_LOSS_GUARD_STREAK=3,
                   RAMON_OPEN_LOSS_GUARD_HOURS=24)
class OpenLossGuardTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.scope = dict(instance_id="demo-ea", account_login="demo-100", symbol="XAUUSD_o")

    def record(self, number, pnl, *, hours_ago=1, **scope):
        args = {**self.scope, **scope}
        return LearningTradeOutcome.objects.create(
            trade_id=f"guard-{number}", realized_pnl=Decimal(str(pnl)),
            closed_at=self.now-timedelta(hours=hours_ago), **args,
        )

    def reason(self, **scope):
        return open_loss_guard_reason(now=self.now, **{**self.scope, **scope})

    def test_three_consecutive_losses_block_new_opens(self):
        self.record(1, -3, hours_ago=3)
        self.record(2, -2, hours_ago=2)
        self.record(3, -1, hours_ago=1)
        self.assertEqual(self.reason(), "OPEN_LOSS_STREAK_LIMIT_REACHED")

    def test_less_than_three_losses_does_not_block(self):
        self.record(1, -3)
        self.record(2, -2)
        self.assertIsNone(self.reason())

    def test_newer_win_or_breakeven_breaks_streak(self):
        self.record(1, -3, hours_ago=4)
        self.record(2, -2, hours_ago=3)
        self.record(3, -1, hours_ago=2)
        self.record(4, 0, hours_ago=1)
        self.assertIsNone(self.reason())

    def test_account_and_symbol_isolation(self):
        for n in range(3):
            self.record(n, -1, hours_ago=n+1)
        self.assertIsNone(self.reason(account_login="demo-200"))
        self.assertIsNone(self.reason(symbol="EURUSD"))
        self.assertIsNone(self.reason(instance_id="other-ea"))

    def test_expired_loss_streak_does_not_block(self):
        for n in range(3):
            self.record(n, -1, hours_ago=25+n)
        self.assertIsNone(self.reason())

    @override_settings(RAMON_OPEN_LOSS_GUARD_ENABLED=False)
    def test_disabled_guard_does_not_block(self):
        for n in range(3):
            self.record(n, -1)
        self.assertIsNone(self.reason())

    @override_settings(RAMON_OPEN_LOSS_GUARD_STREAK=0)
    def test_invalid_configuration_fails_closed(self):
        self.assertEqual(self.reason(), "OPEN_LOSS_GUARD_INVALID_CONFIG")
