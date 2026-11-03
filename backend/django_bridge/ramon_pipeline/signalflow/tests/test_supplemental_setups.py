"""Firing tests for the five supplemental setups, the higher-timeframe bias and the
two-pass (core first, supplemental second) evaluation order."""
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from django.test import override_settings

from ramon_pipeline.signalflow.additional_setups import (
    detect_additional_opportunity,
    detect_all_additional_opportunities,
)
from ramon_pipeline.signalflow.higher_timeframe import (
    BEARISH, BULLISH, UNAVAILABLE, higher_timeframe_bias,
)
from ramon_pipeline.signalflow.opportunities import OpportunityDetector
from ramon_pipeline.signalflow.trading_config import load_trading_config

NAMES = ("failed_breakout_sweep", "range_edge_fade", "session_opening_range_breakout",
         "volatility_compression_expansion", "exhaustion_reversal")


def setups(**enabled):
    base = {
        "failed_breakout_sweep": {"lookback_bars": 20, "sweep_buffer_atr": 0.1},
        "range_edge_fade": {"lookback_bars": 32, "edge_buffer_atr": 0.2, "minimum_rejection_wick_ratio": 0.35},
        "session_opening_range_breakout": {
            "sessions": {"LONDON": {"timezone": "Europe/London", "open": "08:00"}},
            "opening_range_minutes": 30, "breakout_buffer_atr": 0.1},
        "volatility_compression_expansion": {
            "compression_lookback_bars": 20, "compression_max_width_atr": 3.0, "expansion_body_atr": 1.0},
        "exhaustion_reversal": {"extension_lookback_bars": 20, "minimum_extension_atr": 2.5,
                                "rsi_extreme": 75, "require_choch": True},
    }
    return {name: {**base[name], "enabled": bool(enabled.get(name, False))} for name in NAMES}


def flat(n=40, price=100.0, half=1.0):
    return {
        "times": [1_700_000_000 + i * 900 for i in range(n)],
        "opens": [price] * n, "highs": [price + half] * n, "lows": [price - half] * n,
        "closes": [price] * n, "atr": [1.0] * n, "adx": [15.0] * n, "rsi": [50.0] * n,
    }


def detect(d, regime, config, swing_highs=(), swing_lows=()):
    return detect_additional_opportunity(
        d["times"], d["opens"], d["highs"], d["lows"], d["closes"], d["atr"], d["adx"], d["rsi"],
        list(swing_highs), list(swing_lows), regime, config)


class RangeEdgeFadeTests(TestCase):
    def test_upper_rejection_in_a_ranging_regime_sells(self):
        d = flat()
        d["opens"][-1], d["highs"][-1], d["lows"][-1], d["closes"][-1] = 100.2, 101.0, 99.9, 100.0
        result = detect(d, "RANGING", setups(range_edge_fade=True))
        self.assertEqual((result["type"], result["side"]), ("RANGE_EDGE_FADE", "SELL"))

    def test_lower_rejection_buys(self):
        d = flat()
        d["opens"][-1], d["highs"][-1], d["lows"][-1], d["closes"][-1] = 99.8, 100.1, 99.0, 100.0
        result = detect(d, "RANGING", setups(range_edge_fade=True))
        self.assertEqual((result["type"], result["side"]), ("RANGE_EDGE_FADE", "BUY"))

    def test_never_fires_outside_a_ranging_regime(self):
        d = flat()
        d["opens"][-1], d["highs"][-1], d["lows"][-1], d["closes"][-1] = 100.2, 101.0, 99.9, 100.0
        self.assertIsNone(detect(d, "BULLISH", setups(range_edge_fade=True)))


class SessionOpeningRangeTests(TestCase):
    def _session(self, day, start_hour, last_hour, last_close):
        start = datetime(2024, *day, start_hour, tzinfo=timezone.utc).timestamp()
        end = datetime(2024, *day, last_hour, tzinfo=timezone.utc).timestamp()
        times = []
        t = start
        while t <= end:
            times.append(t)
            t += 900
        n = len(times)
        d = {"times": times, "opens": [100.0] * n, "highs": [101.0] * n, "lows": [99.0] * n,
             "closes": [100.0] * n, "atr": [1.0] * n, "adx": [15.0] * n, "rsi": [50.0] * n}
        d["closes"][-1] = last_close
        return d

    def test_winter_london_open_breakout_buys(self):
        d = self._session((1, 15), 6, 9, 102.0)  # London == UTC in January: range 08:00-08:30 UTC
        result = detect(d, "BULLISH", setups(session_opening_range_breakout=True))
        self.assertEqual((result["type"], result["side"], result["session"]),
                         ("SESSION_OPENING_RANGE_BREAKOUT", "BUY", "LONDON"))

    def test_breakdown_sells(self):
        d = self._session((1, 15), 6, 9, 98.0)
        result = detect(d, "BEARISH", setups(session_opening_range_breakout=True))
        self.assertEqual(result["side"], "SELL")

    def test_summer_time_moves_the_opening_range_one_hour_earlier_in_utc(self):
        # July: London is UTC+1, so the range is 07:00-07:30 UTC and a 08:00 UTC bar can break it.
        d = self._session((7, 15), 5, 8, 102.0)
        self.assertEqual(detect(d, "BULLISH", setups(session_opening_range_breakout=True))["side"], "BUY")

    def test_no_signal_while_the_opening_range_is_still_forming(self):
        d = self._session((1, 15), 6, 8, 102.0)  # last bar is the 08:00 bar itself
        self.assertIsNone(detect(d, "BULLISH", setups(session_opening_range_breakout=True)))


class CompressionExpansionTests(TestCase):
    def _tight(self):
        d = flat(half=0.2)  # 20-bar width 0.4 ATR
        return d

    def test_tight_range_then_breakout_candle_buys(self):
        d = self._tight()
        d["opens"][-1], d["closes"][-1], d["highs"][-1], d["lows"][-1] = 100.0, 101.5, 101.6, 99.95
        result = detect(d, "BULLISH", setups(volatility_compression_expansion=True))
        self.assertEqual((result["type"], result["side"]), ("VOLATILITY_COMPRESSION_EXPANSION", "BUY"))

    def test_tight_range_then_breakdown_candle_sells(self):
        d = self._tight()
        d["opens"][-1], d["closes"][-1], d["highs"][-1], d["lows"][-1] = 100.0, 98.5, 100.05, 98.4
        self.assertEqual(detect(d, "BEARISH", setups(volatility_compression_expansion=True))["side"], "SELL")

    def test_wide_prior_range_is_not_compression(self):
        # Regression: the old formula allowed width <= 0.75 x ATR x 20 bars (= 15 ATR),
        # so this 10-ATR-wide range counted as "compressed".
        d = flat(half=5.0)
        d["opens"][-1], d["closes"][-1], d["highs"][-1], d["lows"][-1] = 100.0, 106.5, 106.6, 99.9
        self.assertIsNone(detect(d, "BULLISH", setups(volatility_compression_expansion=True)))

    def test_big_candle_that_stays_inside_the_range_is_not_an_expansion(self):
        d = flat(half=1.2)  # width 2.4 ATR: compressed, but the candle closes inside it
        d["opens"][-1], d["closes"][-1], d["highs"][-1], d["lows"][-1] = 99.0, 100.2, 100.3, 98.9
        self.assertIsNone(detect(d, "BULLISH", setups(volatility_compression_expansion=True)))


class ExhaustionReversalTests(TestCase):
    def test_extended_up_move_with_choch_sells(self):
        d = flat()
        d["closes"][-1] = 103.0
        d["highs"][-1] = 103.4
        d["rsi"][-1] = 80.0
        d["lows"][30] = 103.5  # last confirmed swing low, now broken by the close
        result = detect(d, "BULLISH", setups(exhaustion_reversal=True), swing_lows=[30])
        self.assertEqual((result["type"], result["side"]), ("EXHAUSTION_REVERSAL", "SELL"))

    def test_extended_down_move_with_choch_buys(self):
        d = flat()
        d["closes"][-1] = 97.0
        d["lows"][-1] = 96.6
        d["rsi"][-1] = 20.0
        d["highs"][30] = 96.5
        result = detect(d, "BEARISH", setups(exhaustion_reversal=True), swing_highs=[30])
        self.assertEqual((result["type"], result["side"]), ("EXHAUSTION_REVERSAL", "BUY"))

    def test_no_choch_no_signal(self):
        d = flat()
        d["closes"][-1], d["rsi"][-1] = 103.0, 80.0
        self.assertIsNone(detect(d, "BULLISH", setups(exhaustion_reversal=True), swing_lows=[]))


class AllSetupsAreEvaluatedTests(TestCase):
    def _double_trigger(self):
        d = flat(half=0.2)
        d["opens"][-1], d["highs"][-1], d["lows"][-1], d["closes"][-1] = 100.2, 100.6, 98.9, 99.0
        return d

    def test_a_later_setup_is_not_hidden_by_an_earlier_one(self):
        d = self._double_trigger()
        config = setups(failed_breakout_sweep=True, volatility_compression_expansion=True)
        found = detect_all_additional_opportunities(
            d["times"], d["opens"], d["highs"], d["lows"], d["closes"], d["atr"], d["adx"], d["rsi"],
            [], [], "BEARISH", config)
        self.assertEqual({c["type"] for c in found},
                         {"FAILED_BREAKOUT_SWEEP", "VOLATILITY_COMPRESSION_EXPANSION"})

    def test_the_best_scoring_setup_wins_and_the_other_is_reported(self):
        d = self._double_trigger()
        config = setups(failed_breakout_sweep=True, volatility_compression_expansion=True)
        best = detect(d, "BEARISH", config)
        self.assertEqual(best["type"], "VOLATILITY_COMPRESSION_EXPANSION")
        self.assertEqual(best["also_detected"], ["FAILED_BREAKOUT_SWEEP"])

    def test_disabled_setups_never_fire(self):
        d = self._double_trigger()
        self.assertIsNone(detect(d, "BEARISH", setups()))


class HigherTimeframeBiasTests(TestCase):
    START = 1_704_067_200  # 2024-01-01 00:00 UTC, an hour boundary

    def _series(self, n, step):
        times = [self.START + i * 900 for i in range(n)]
        closes = [100.0 + i * step for i in range(n)]
        return times, closes

    def test_rising_hourly_closes_are_bullish(self):
        times, closes = self._series(400, 0.05)
        self.assertEqual(higher_timeframe_bias(times, closes, 900), BULLISH)

    def test_falling_hourly_closes_are_bearish(self):
        times, closes = self._series(400, -0.05)
        self.assertEqual(higher_timeframe_bias(times, closes, 900), BEARISH)

    def test_the_forming_hour_is_ignored(self):
        times, closes = self._series(398, 0.05)  # last hour has only 2 of 4 candles
        full_times, full_closes = self._series(396, 0.05)
        self.assertEqual(higher_timeframe_bias(times, closes, 900),
                         higher_timeframe_bias(full_times, full_closes, 900))

    def test_too_little_history_is_unavailable(self):
        times, closes = self._series(40, 0.05)
        self.assertEqual(higher_timeframe_bias(times, closes, 900), UNAVAILABLE)


class EvaluationOrderTests(TestCase):
    MARKET = {"symbol": "XAUUSD", "timeframe": "M15", "bars": [{"time": 0}]}

    def test_an_approved_core_signal_is_returned_without_consulting_supplemental(self):
        detector = OpportunityDetector()
        with patch.object(OpportunityDetector, "_evaluate_structure",
                          return_value={"approved": True, "reason": "PULLBACK"}) as mocked:
            result = detector.evaluate(self.MARKET)
        self.assertEqual(result["reason"], "PULLBACK")
        mocked.assert_called_once()
        self.assertFalse(mocked.call_args.kwargs.get("include_supplemental", True))

    def test_supplemental_is_used_only_after_the_core_path_declined(self):
        detector = OpportunityDetector()
        core = {"approved": False, "status": "REJECTED", "reason": "MARKET_RANGING"}
        sup = {"approved": True, "status": "ACCEPTED", "reason": "RANGE_EDGE_FADE_CONFIRMED"}
        with patch.object(OpportunityDetector, "_evaluate_structure", side_effect=[core, sup]):
            result = detector.evaluate(self.MARKET)
        self.assertEqual(result["reason"], "RANGE_EDGE_FADE_CONFIRMED")
        self.assertEqual(result["core_rejection"]["reason"], "MARKET_RANGING")

    def test_when_nothing_fires_the_core_verdict_is_kept(self):
        detector = OpportunityDetector()
        core = {"approved": False, "status": "DEFERRED", "reason": "MARKET_UNCERTAIN"}
        with patch.object(OpportunityDetector, "_evaluate_structure", side_effect=[core, None]):
            result = detector.evaluate(self.MARKET)
        self.assertEqual(result["reason"], "MARKET_UNCERTAIN")


class ConfigAndProfileTests(TestCase):
    def test_compression_uses_total_width_and_htf_is_off_by_default(self):
        config = load_trading_config()
        compression = config["opportunity_finder"]["setups"]["volatility_compression_expansion"]
        self.assertIn("compression_max_width_atr", compression)
        self.assertNotIn("compression_max_atr", compression)
        htf = config["opportunity_finder"]["higher_timeframe_bias"]
        self.assertFalse(htf["enabled"])
        self.assertFalse(htf["demo_enabled"])

    def test_invalid_htf_settings_fail_closed(self):
        config = json.loads(Path(__file__).parents[1].joinpath("trading_config.json").read_text())
        config["opportunity_finder"]["higher_timeframe_bias"]["ema_period"] = 1
        with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8") as file:
            json.dump(config, file)
            file.flush()
            with self.assertRaises(ValueError):
                load_trading_config(file.name)

    def test_demo_profile_switch_keeps_live_thresholds_on_demo_when_asked(self):
        from ea_bridge.decision import _build_pipeline
        with override_settings(RAMON_DEMO_PROFILE="standard"):
            standard = _build_pipeline(demo_mode=True)
        with override_settings(RAMON_DEMO_PROFILE="expanded"):
            expanded = _build_pipeline(demo_mode=True)
        self.assertEqual(standard.opportunities.minimum_score, 0.55)
        self.assertEqual(expanded.opportunities.minimum_score, 0.48)
        for pipeline in (standard, expanded):  # supplemental setups stay on for demo either way
            setups_on = pipeline.opportunities.trading_settings["opportunity_finder"]["setups"]
            self.assertTrue(setups_on["failed_breakout_sweep"]["enabled"])
