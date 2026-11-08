import math
from unittest import TestCase

from ramon_pipeline.signalflow.additional_setups import detect_additional_opportunity
from ramon_pipeline.signalflow import opportunities as opportunities_module
from ramon_pipeline.signalflow.opportunities import OpportunityConfig


def _setups(**overrides):
    names = {
        "failed_breakout_sweep": {"enabled": False, "lookback_bars": 20, "sweep_buffer_atr": 0.1},
        "range_edge_fade": {
            "enabled": True, "lookback_bars": 32, "edge_buffer_atr": 0.2,
            "minimum_rejection_wick_ratio": 0.35,
            "minimum_range_width_atr": 2.0, "maximum_range_width_atr": 12.0,
            "boundary_touch_tolerance_atr": 0.35, "minimum_boundary_touches": 2,
            "minimum_close_reentry_atr": 0.05,
            "maximum_sell_close_location": 0.45, "minimum_buy_close_location": 0.55,
        },
        "session_opening_range_breakout": {"enabled": False, "sessions": {}},
        "volatility_compression_expansion": {"enabled": False},
        "exhaustion_reversal": {"enabled": False},
    }
    names["range_edge_fade"].update(overrides)
    return names


def _market(side):
    times = [1_700_000_000 + i * 900 for i in range(40)]
    opens, highs, lows, closes = [], [], [], []
    # Build a ten-point, two-sided range with repeated reactions at both edges.
    for i in range(39):
        if i % 4 == 0:
            o, h, lo, c = 109.0, 110.0, 108.0, 109.2
        elif i % 4 == 1:
            o, h, lo, c = 101.0, 102.0, 100.0, 101.2
        else:
            o, h, lo, c = 104.5, 106.0, 104.0, 105.0
        opens.append(o); highs.append(h); lows.append(lo); closes.append(c)
    if side == "SELL":
        opens.append(109.5); highs.append(110.3); lows.append(108.5); closes.append(108.8)
    else:
        opens.append(100.5); highs.append(101.8); lows.append(99.7); closes.append(101.2)
    atr = [2.0] * 40
    adx = [8.0] * 40
    rsi = [50.0] * 40
    return times, opens, highs, lows, closes, atr, adx, rsi


class RangeEdgeFadeTests(TestCase):
    def detect(self, side, **settings):
        values = _market(side)
        return detect_additional_opportunity(
            *values, [], [], "RANGING", _setups(**settings)
        )

    def test_upper_boundary_rejection_creates_sell_candidate(self):
        candidate = self.detect("SELL")
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["type"], "RANGE_EDGE_FADE")
        self.assertEqual(candidate["side"], "SELL")
        self.assertGreaterEqual(candidate["boundary_touches"]["upper"], 2)
        self.assertGreaterEqual(candidate["boundary_touches"]["lower"], 2)
        self.assertLess(candidate["close_location"], 0.45)

    def test_lower_boundary_rejection_creates_buy_candidate(self):
        candidate = self.detect("BUY")
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["type"], "RANGE_EDGE_FADE")
        self.assertEqual(candidate["side"], "BUY")
        self.assertGreaterEqual(candidate["boundary_touches"]["upper"], 2)
        self.assertGreaterEqual(candidate["boundary_touches"]["lower"], 2)
        self.assertGreater(candidate["close_location"], 0.55)

    def test_range_fade_is_not_created_without_a_ranging_regime(self):
        values = _market("SELL")
        self.assertIsNone(detect_additional_opportunity(
            *values, [], [], "BULLISH", _setups()
        ))

    def test_range_fade_rejects_weak_rejection_wick(self):
        candidate = self.detect("SELL", minimum_rejection_wick_ratio=0.8)
        self.assertIsNone(candidate)

    def test_range_fade_rejects_ranges_that_are_too_narrow(self):
        candidate = self.detect("SELL", minimum_range_width_atr=6.0)
        self.assertIsNone(candidate)

    def test_regime_detector_uses_low_directional_efficiency_to_confirm_ranges(self):
        count = 120
        close = [100.0 + 2.0 * math.sin(i * 0.55) for i in range(count)]
        bars = opportunities_module._Bars(
            times=[1_700_000_000 + i * 900 for i in range(count)],
            high=[value + 0.5 for value in close],
            low=[value - 0.5 for value in close],
            close=close,
            interval=900.0,
        )
        config = OpportunityConfig()
        indicators = opportunities_module._compute_indicators(bars, config)
        regime = opportunities_module._classify_regime(bars, indicators, count - 1, config)
        self.assertLess(regime.range_efficiency, 0.28)
        self.assertEqual(regime.label, "RANGING")
