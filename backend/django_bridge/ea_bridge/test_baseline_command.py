from datetime import date, timedelta
from io import StringIO

from django.core.management import call_command, CommandError
from django.test import TestCase

from .models import AccountDailyBaseline


class CaptureDailyBaselineCommandTests(TestCase):
    def _run(self, **overrides):
        options = {
            "account_login": "91422263",
            "server_name": "LiteFinance-MT5-Demo",
            "broker_day": date.today().isoformat(),
            "opening_balance": "2394.39",
            "source_instance_id": "ramon-command-ea-1",
            "confirm_broker_opening_balance": True,
        }
        options.update(overrides)
        output = StringIO()
        call_command("capture_daily_baseline", stdout=output, **options)
        return output.getvalue()

    def test_creates_only_explicit_broker_verified_baseline(self):
        output = self._run()
        baseline = AccountDailyBaseline.objects.get(
            account_login="91422263",
            server_name="LiteFinance-MT5-Demo",
            broker_day=date.today(),
        )
        self.assertEqual(str(baseline.opening_balance), "2394.39")
        self.assertEqual(baseline.source_instance_id, "ramon-command-ea-1")
        self.assertIn("Trading authorization remains unchanged", output)

    def test_requires_explicit_broker_verification_acknowledgement(self):
        with self.assertRaisesMessage(
            CommandError, "--confirm-broker-opening-balance"
        ):
            self._run(confirm_broker_opening_balance=False)
        self.assertEqual(AccountDailyBaseline.objects.count(), 0)

    def test_does_not_overwrite_existing_baseline(self):
        AccountDailyBaseline.objects.create(
            account_login="91422263",
            server_name="LiteFinance-MT5-Demo",
            broker_day=date.today(),
            opening_balance="2300.00",
            source_instance_id="verified-source",
        )
        with self.assertRaisesMessage(CommandError, "Baselines are immutable"):
            self._run(opening_balance="2394.39")
        baseline = AccountDailyBaseline.objects.get(
            account_login="91422263",
            server_name="LiteFinance-MT5-Demo",
            broker_day=date.today(),
        )
        self.assertEqual(str(baseline.opening_balance), "2300.00")
        self.assertEqual(baseline.source_instance_id, "verified-source")

    def test_rejects_nonpositive_or_nonfinite_balance(self):
        for value in ("0", "-1", "NaN", "Infinity"):
            with self.subTest(value=value):
                with self.assertRaisesMessage(CommandError, "positive finite decimal"):
                    self._run(opening_balance=value)
        self.assertEqual(AccountDailyBaseline.objects.count(), 0)

    def test_rejects_future_broker_day(self):
        future_day = (date.today() + timedelta(days=1)).isoformat()
        with self.assertRaisesMessage(CommandError, "cannot be in the future"):
            self._run(broker_day=future_day)
        self.assertEqual(AccountDailyBaseline.objects.count(), 0)
