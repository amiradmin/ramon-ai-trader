"""Pure/stateless and PostgreSQL-backed checks for yearly validation safety."""
import io
import json
from datetime import datetime, timezone
from unittest import TestCase

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase as DatabaseTestCase, override_settings

from ea_bridge.walk_forward_stats import expectancy_summary, grouped_summary, trading_session
from ea_bridge.models import EAHeartbeat, MarketBar
from ea_bridge.management.commands.evaluate_walk_forward_years import read_bars, yearly_coverage_issues


class PerformanceSummaryTests(TestCase):
    def test_yearly_coverage_rejects_truncated_and_missing_series(self):
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        end = datetime(2026, 1, 1, tzinfo=timezone.utc)
        series = {"M15": [{"time": datetime(2025, 2, 1, tzinfo=timezone.utc).timestamp()}],
                  "M5": [], "H1": [{"time": start.timestamp()}, {"time": (end.timestamp() - 3600)}]}
        issues = yearly_coverage_issues(series, start, end)
        self.assertIn("M15:LATE_START", issues)
        self.assertIn("M15:EARLY_END", issues)
        self.assertIn("M5:NO_YEAR_DATA", issues)
        self.assertFalse(any(item.startswith("H1:") for item in issues))

    def test_small_samples_do_not_claim_significance(self):
        trades = [{"net_r": 1.25}, {"net_r": -1}]
        result = expectancy_summary(trades)
        self.assertEqual(result["trades"], 2)
        self.assertEqual(result["ci95_r"], None)
        self.assertEqual(result["ci_method"], "INSUFFICIENT_TRADES_LT_30")

    def test_closed_trade_drawdown_streak_and_holding_time(self):
        trades = [{"net_r": r, "entry_time": 0, "exit_time": 900} for r in (2, -1, -2, 1)]
        result = expectancy_summary(trades)
        self.assertEqual(result['max_closed_trade_drawdown_r'], 3)
        self.assertEqual(result['max_consecutive_losses'], 2)
        self.assertEqual(result['average_win_r'], 1.5)
        self.assertEqual(result['average_loss_r'], -1.5)
        self.assertEqual(result['average_holding_seconds'], 900)

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


@override_settings(RAMON_REAL_CENT_INSTANCE='historical-test',RAMON_REAL_CENT_LOGIN='123',RAMON_REAL_CENT_SERVER='Broker-Live',RAMON_REAL_CENT_CURRENCY='USC',RAMON_REAL_CENT_SYMBOL='XAUUSD_o')
class YearlyWalkForwardSafetyTests(DatabaseTestCase):
    def test_requires_explicit_demo_contract(self):
        with self.assertRaisesMessage(CommandError, "configured real cent"):
            call_command("evaluate_walk_forward_years", source="unknown",
                         first_year=2022, last_year=2022,
                         spread_points=22, slippage_points=5, commission_points=5,
                         stdout=io.StringIO())

    def test_missing_history_remains_insufficient_not_a_fake_zero_trade_success(self):
        EAHeartbeat.objects.create(
            instance_id="historical-test", account_login="123", server_name="Broker-Live", symbol="XAUUSD_o",
            payload={"account_trade_mode": 2,"server_name":"Broker-Live","account":{"currency":"USC"}, "quote": {"point": "0.01"},
                     "symbol_info": {"tick_size": "0.01", "tick_value": "1",
                                     "volume_step": "0.01", "volume_min": "0.01",
                                     "volume_max": "100", "stops_level_points": "0"}},
        )
        output = io.StringIO()
        call_command("evaluate_walk_forward_years", source="historical-test", symbol="XAUUSD_o",
                     first_year=2022, last_year=2022, spread_points=22,
                     slippage_points=5, commission_points=5, stdout=output)
        report = json.loads(output.getvalue())
        self.assertEqual(report["folds"][0]["status"], "INSUFFICIENT_HISTORY")
        self.assertEqual(report["execution"], "READ_ONLY_NO_COMMANDS")

    def test_off_grid_history_requires_explicit_diagnostic_override(self):
        from datetime import timedelta
        start = datetime(2026, 10, 26, tzinfo=timezone.utc)
        MarketBar.objects.create(
            source="historical-test", symbol="XAUUSD_o", timeframe="M15",
            timestamp=start + timedelta(hours=2, minutes=15, seconds=1),
            open=2300, high=2301, low=2299, close=2300)
        with self.assertRaisesMessage(CommandError, "OFF_GRID_M15"):
            read_bars(source="historical-test", symbol="XAUUSD_o",
                      timeframe="M15", start=start, end=start+timedelta(days=1),
                      maximum=100)
        bars, digest, quality = read_bars(
            source="historical-test", symbol="XAUUSD_o",
            timeframe="M15", start=start, end=start+timedelta(days=1),
            maximum=100, allow_off_grid=True)
        self.assertEqual(bars, [])
        self.assertEqual(quality["input_rows"], 1)
        self.assertEqual(quality["off_grid_excluded"], 1)
        self.assertEqual(quality["offset_seconds_counts"], {"1.0": 1})
        self.assertEqual(MarketBar.objects.count(), 1)

    def test_aligned_history_is_preserved_with_diagnostic_override(self):
        from datetime import timedelta
        start = datetime(2026, 10, 26, tzinfo=timezone.utc)
        MarketBar.objects.create(
            source="historical-test", symbol="XAUUSD_o", timeframe="M15",
            timestamp=start + timedelta(hours=2, minutes=15),
            open=2300, high=2301, low=2299, close=2300)
        bars, digest, quality = read_bars(
            source="historical-test", symbol="XAUUSD_o",
            timeframe="M15", start=start, end=start+timedelta(days=1),
            maximum=100, allow_off_grid=True)
        self.assertEqual(len(bars), 1)
        self.assertEqual(quality["off_grid_excluded"], 0)
        self.assertEqual(quality["accepted_rows"], 1)
