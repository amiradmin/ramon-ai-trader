"""Pure/stateless and PostgreSQL-backed checks for yearly validation safety."""
import io
import json
from datetime import datetime, timezone
from unittest import TestCase

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase as DatabaseTestCase

from ea_bridge.walk_forward_stats import expectancy_summary, grouped_summary, trading_session
from ea_bridge.models import EAHeartbeat


class PerformanceSummaryTests(TestCase):
    def test_small_samples_do_not_claim_significance(self):
        trades = [{"net_r": 1.25}, {"net_r": -1}]
        result = expectancy_summary(trades)
        self.assertEqual(result["trades"], 2)
        self.assertEqual(result["ci95_r"], None)
        self.assertEqual(result["ci_method"], "INSUFFICIENT_TRADES_LT_30")

    def test_bootstrap_is_reproducible_and_descriptive(self):
        trades = [{"net_r": 1.25 if i % 3 == 0 else -1.0} for i in range(60)]
        one = expectancy_summary(trades)
        two = expectancy_summary(trades)
        self.assertEqual(one, two)
        self.assertEqual(len(one["ci95_r"]), 2)

    def test_sessions_respect_london_dst_and_overlap(self):
        self.assertEqual(trading_session(datetime(2026, 7, 15, 8, tzinfo=timezone.utc).timestamp()), "LONDON")
        self.assertEqual(trading_session(datetime(2026, 7, 15, 13, tzinfo=timezone.utc).timestamp()), "LONDON_NEW_YORK_OVERLAP")
        self.assertEqual(trading_session(datetime(2026, 1, 15, 14, tzinfo=timezone.utc).timestamp()), "LONDON_NEW_YORK_OVERLAP")

    def test_groups_are_partitioned_without_duplication(self):
        trades = [
            {"setup": "TREND", "entry_time": datetime(2026, 7, 15, 8, tzinfo=timezone.utc).timestamp(), "net_r": 1.0},
            {"setup": "RANGE", "entry_time": datetime(2026, 7, 15, 13, tzinfo=timezone.utc).timestamp(), "net_r": -1.0},
        ]
        report = grouped_summary(trades)
        self.assertEqual(report["all"]["trades"], 2)
        self.assertEqual(sum(item["trades"] for item in report["setups"].values()), 2)
        self.assertEqual(sum(item["trades"] for item in report["sessions"].values()), 2)


class YearlyWalkForwardSafetyTests(DatabaseTestCase):
    def test_requires_explicit_demo_contract(self):
        with self.assertRaisesMessage(CommandError, "explicitly demo"):
            call_command("evaluate_walk_forward_years", source="unknown",
                         first_year=2022, last_year=2022,
                         spread_points=22, slippage_points=5, commission_points=5,
                         stdout=io.StringIO())

    def test_missing_history_remains_insufficient_not_a_fake_zero_trade_success(self):
        EAHeartbeat.objects.create(
            instance_id="historical-test", symbol="XAUUSD_o",
            payload={"account_trade_mode": 0, "quote": {"point": "0.01"},
                     "symbol_info": {"tick_size": "0.01", "tick_value": "1",
                                     "volume_step": "0.01", "volume_min": "0.01",
                                     "volume_max": "100", "stops_level_points": "0"}},
        )
        output = io.StringIO()
        call_command("evaluate_walk_forward_years", source="historical-test",
                     first_year=2022, last_year=2022, spread_points=22,
                     slippage_points=5, commission_points=5, stdout=output)
        report = json.loads(output.getvalue())
        self.assertEqual(report["folds"][0]["status"], "INSUFFICIENT_HISTORY")
        self.assertEqual(report["execution"], "READ_ONLY_NO_COMMANDS")
