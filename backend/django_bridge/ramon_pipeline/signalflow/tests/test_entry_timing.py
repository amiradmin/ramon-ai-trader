from unittest import TestCase

from ramon_pipeline.signalflow.entry_timing import EntryTimer, EntryTimingConfig


class EntryTimerTests(TestCase):
    def setUp(self):
        self.opportunity = {
            "approved": True,
            "side": "BUY",
            "signal_id": "sig-123",
            "signal_bar_time": 1_800_000_000,
            "setup": {
                "atr": 10.0,
                "reference_price": 2000.0,
                "invalidation_price": 1980.0,
            },
        }

    def test_legacy_contract_still_accepts_valid_quality_and_spread(self):
        result = EntryTimer().evaluate(
            {"approved": True, "side": "BUY", "signal_id": "legacy-1"},
            {"entry_quality": 0.8, "spread_points": 20},
        )
        self.assertTrue(result["approved"])
        self.assertEqual(result["reason"], "ENTRY_TIMING_APPROVED")
        self.assertEqual(result["status"], "ACCEPTED")

    def test_legacy_missing_spread_preserves_spread_reason(self):
        result = EntryTimer().evaluate(
            {"approved": True, "side": "BUY"},
            {"entry_quality": 0.8},
        )
        self.assertFalse(result["approved"])
        self.assertEqual(result["reason"], "SPREAD_OUT_OF_RANGE")

    def test_structured_buy_accepts_quote_with_room_and_tight_spread(self):
        result = EntryTimer().evaluate(self.opportunity, {
            "bid": 2001.0, "ask": 2001.2, "point": 0.1,
            "spread_points": 2, "timeframe": "M15",
            "latest_bar_time": 1_800_000_000,
        })
        self.assertTrue(result["approved"], result)
        self.assertEqual(result["timing"], "ENTER_NOW")
        self.assertEqual(result["idempotency_key"], "entry:sig-123")
        self.assertEqual(result["price_checks"], "APPLIED")

    def test_structured_setup_rejects_invalidated_buy(self):
        result = EntryTimer().evaluate(self.opportunity, {
            "bid": 1979.0, "ask": 1979.2, "point": 0.1,
            "timeframe": "M15",
        })
        self.assertFalse(result["approved"])
        self.assertEqual(result["reason"], "SETUP_INVALIDATED")

    def test_structured_setup_defers_when_price_has_run_away(self):
        result = EntryTimer().evaluate(self.opportunity, {
            "bid": 2007.0, "ask": 2007.2, "point": 0.1,
            "timeframe": "M15",
        })
        self.assertFalse(result["approved"])
        self.assertEqual(result["status"], "DEFERRED")
        self.assertEqual(result["reason"], "PRICE_EXTENDED_FROM_SIGNAL")

    def test_structured_setup_rejects_future_signal_time(self):
        opportunity = {**self.opportunity, "signal_bar_time": 1_800_001_000}
        result = EntryTimer().evaluate(opportunity, {
            "bid": 2001.0, "ask": 2001.2, "point": 0.1,
            "timeframe": "M15", "latest_bar_time": 1_800_000_000,
        })
        self.assertFalse(result["approved"])
        self.assertEqual(result["reason"], "SIGNAL_TIME_INVALID")

    def test_quote_must_be_complete_and_valid(self):
        result = EntryTimer().evaluate(self.opportunity, {"bid": 2000.0, "ask": 2000.1})
        self.assertFalse(result["approved"])
        self.assertEqual(result["reason"], "INVALID_QUOTE")

    def test_non_finite_metrics_fail_closed(self):
        result = EntryTimer().evaluate(
            {"approved": True, "side": "BUY"},
            {"entry_quality": float("nan"), "spread_points": 1},
        )
        self.assertFalse(result["approved"])
        self.assertEqual(result["reason"], "INVALID_ENTRY_METRICS")

    def test_require_quote_blocks_structured_setup_without_quote(self):
        result = EntryTimer(require_quote=True).evaluate(
            self.opportunity, {"spread_points": 1}
        )
        self.assertFalse(result["approved"])
        self.assertEqual(result["reason"], "QUOTE_REQUIRED")

    def test_config_rejects_non_finite_thresholds(self):
        with self.assertRaises(ValueError):
            EntryTimingConfig(max_chase_atr=float("inf"))
