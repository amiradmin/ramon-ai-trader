from types import SimpleNamespace
from unittest import TestCase

from ramon_pipeline.signalflow.replay import replay_strategy


class ReplayTests(TestCase):
    def setUp(self):
        self.bars = [{"time": 1_700_000_000//900*900+i*900, "open": 100.0, "high": 100.5,
                      "low": 99.5, "close": 100.0} for i in range(132)]
        self.account = {"point": 0.01, "point_value_per_lot": 1.0, "account_balance": 10000.0}

    def run_replay(self, *, side="BUY", spread=0, slip=0, commission=0, context=None):
        calls = []

        class Pipeline:
            risk_manager = SimpleNamespace(maximum_risk_percent=0.5,
                                           config=SimpleNamespace(target_reward_risk=2.0, min_reward_risk=1.2))

            def run(self, market):
                calls.append(market)
                if len(calls) != 1:
                    return {"approved": False, "reason": "NO_SETUP"}
                return {"approved": True, "action": side, "stages": {
                    "opportunities": {"setup": {"type": "TEST"}},
                    "risk_management": {"stop_price": 99 if side == "BUY" else 101,
                                        "target_price": 102 if side == "BUY" else 98, "volume": 1.0}}}

        result = replay_strategy(Pipeline, self.bars, context_bars=context or {}, account=self.account,
                                 spread_points=spread, slippage_points=slip, commission_points=commission)
        return result, calls

    def test_both_levels_in_one_candle_use_stop_first_with_costs(self):
        self.bars[128].update(high=103, low=98)
        result, _ = self.run_replay(slip=10, commission=5)
        trade = result["trades"][0]
        self.assertEqual(trade["exit_reason"], "STOP")
        self.assertAlmostEqual(trade["net_r"], -1.25)
        self.assertEqual(result["ending_balance"], 9875)

    def test_gap_stop_uses_worse_open_instead_of_an_impossible_stop_fill(self):
        self.bars[129].update(open=97, high=98, low=96, close=97)
        result, _ = self.run_replay()
        self.assertEqual(result["trades"][0]["exit_reason"], "STOP_GAP")
        self.assertEqual(result["trades"][0]["exit"], 97)
        self.assertEqual(result["trades"][0]["net_r"], -3)

    def test_short_stops_trigger_on_ask_not_bid(self):
        self.bars[128].update(high=100.9)
        result, _ = self.run_replay(side="SELL", spread=20)
        self.assertEqual(result["trades"][0]["exit_reason"], "STOP")

    def test_signals_never_receive_future_or_incomplete_candles(self):
        context = {"M5": [{"time": self.bars[127]["time"]+i*300} for i in range(20)]}
        _, calls = self.run_replay(context=context)
        for market in calls:
            self.assertLess(market["bars"][-1]["time"], market["decision_time"])
            self.assertTrue(all(bar["time"]+300 <= market["decision_time"] for bar in market["context_bars"]["M5"]))

    def test_future_price_changes_do_not_change_the_entry_plan(self):
        before, calls_before = self.run_replay()
        self.bars[129].update(high=110, low=98)
        after, calls_after = self.run_replay()
        self.assertEqual(calls_before[0], calls_after[0])
        self.assertEqual(before["trades"][0]["entry"], after["trades"][0]["entry"])
        self.assertNotEqual(before["trades"][0]["net_r"], after["trades"][0]["net_r"])

    def test_invalid_costs_and_unordered_data_are_rejected(self):
        with self.assertRaises(ValueError):
            self.run_replay(slip=-1)
        self.bars[129]["time"] = self.bars[128]["time"]
        with self.assertRaises(ValueError):
            self.run_replay()
