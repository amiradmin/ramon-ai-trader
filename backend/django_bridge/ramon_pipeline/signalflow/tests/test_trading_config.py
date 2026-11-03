import json
import tempfile
from pathlib import Path
from unittest import TestCase

from ramon_pipeline.signalflow.trading_config import load_trading_config
from ramon_pipeline.signalflow.additional_setups import detect_additional_opportunity
from ramon_pipeline.signalflow.entry_timing import EntryTimer
from ramon_pipeline.signalflow.risk_management import RiskManager
from ramon_pipeline.signalflow.opportunities import OpportunityDetector


class TradingConfigTests(TestCase):
    def test_checked_in_config_loads_and_keeps_new_setups_disabled(self):
        config = load_trading_config()
        setups = config["opportunity_finder"]["setups"]
        self.assertFalse(setups["failed_breakout_sweep"]["enabled"])
        self.assertFalse(setups["range_edge_fade"]["enabled"])
        self.assertFalse(setups["session_opening_range_breakout"]["enabled"])
        self.assertFalse(setups["volatility_compression_expansion"]["enabled"])
        self.assertFalse(setups["exhaustion_reversal"]["enabled"])

    def test_supplemental_setups_are_enabled_only_for_verified_demo_profile(self):
        standard = OpportunityDetector()
        demo = OpportunityDetector(demo_mode=True)
        setup_names = (
            "failed_breakout_sweep",
            "range_edge_fade",
            "session_opening_range_breakout",
            "volatility_compression_expansion",
            "exhaustion_reversal",
        )
        standard_setups = standard.trading_settings["opportunity_finder"]["setups"]
        demo_setups = demo.trading_settings["opportunity_finder"]["setups"]
        for name in setup_names:
            self.assertFalse(standard_setups[name]["enabled"], name)
            self.assertTrue(demo_setups[name]["enabled"], name)

    def test_demo_switches_must_be_boolean(self):
        config = json.loads(Path(__file__).parents[1].joinpath("trading_config.json").read_text())
        config["opportunity_finder"]["setups"]["failed_breakout_sweep"]["demo_enabled"] = "yes"
        with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8") as file:
            json.dump(config, file)
            file.flush()
            with self.assertRaises(ValueError):
                load_trading_config(file.name)

    def test_entry_and_risk_stages_load_the_central_thresholds(self):
        self.assertEqual(EntryTimer().config.max_chase_atr, 0.5)
        self.assertEqual(EntryTimer().maximum_spread_points, 50.0)
        self.assertEqual(RiskManager().config.min_reward_risk, 1.2)
        self.assertEqual(RiskManager().maximum_risk_percent, 0.5)

    def test_lookahead_cannot_be_enabled(self):
        config = json.loads(Path(__file__).parents[1].joinpath("trading_config.json").read_text())
        config["validation"]["allow_lookahead"] = True
        with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8") as file:
            json.dump(config, file)
            file.flush()
            with self.assertRaises(ValueError):
                load_trading_config(file.name)

    def test_failed_breakout_sweep_detects_close_back_inside(self):
        n = 40
        times = [1_700_000_000 + i * 900 for i in range(n)]
        closes = [100.0] * n
        highs = [101.0] * n
        lows = [99.0] * n
        opens = [100.0] * n
        highs[-1], closes[-1] = 102.0, 100.5
        atr = [1.0] * n
        adx = [15.0] * n
        rsi = [50.0] * n
        setups = {name: {"enabled": False} for name in (
            "failed_breakout_sweep", "range_edge_fade", "session_opening_range_breakout",
            "volatility_compression_expansion", "exhaustion_reversal")}
        setups["failed_breakout_sweep"] = {"enabled": True, "lookback_bars": 20,
            "sweep_buffer_atr": 0.1, "max_reclaim_bars": 2}
        result = detect_additional_opportunity(times, opens, highs, lows, closes, atr, adx, rsi, [], [], "RANGING", setups)
        self.assertEqual(result["type"], "FAILED_BREAKOUT_SWEEP")
        self.assertEqual(result["side"], "SELL")
        self.assertGreaterEqual(result["score"], 0.55)
