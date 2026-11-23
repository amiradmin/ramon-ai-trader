from types import SimpleNamespace
from unittest import TestCase

from ramon_pipeline.signalflow.replay import replay_strategy


class ReplayTests(TestCase):
    def setUp(self):
        self.bars = [{"time": 1_700_000_000//900*900+i*900, "open": 100.0, "high": 100.5,
                      "low": 99.5, "close": 100.0} for i in range(132)]
        self.account = {"point": 0.01, "point_value_per_lot": 1.0, "account_balance": 10000.0}

    def run_replay(self, *, side="BUY", spread=0, slip=0, commission=0, context=None, weekly=False, **replay_options):
        calls = []

        class Pipeline:
            use_previous_week = weekly
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
                                 spread_points=spread, slippage_points=slip, commission_points=commission,
                                 **replay_options)
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

    def test_bar_open_spread_updates_short_exit_without_future_leakage(self):
        for bar in self.bars:
            bar["spread_points"] = 10
        self.bars[128].update(high=100.5, spread_points=10)
        self.bars[129].update(high=100.8, spread_points=30)
        result, calls = self.run_replay(side="SELL", spread_mode="bar_open")
        self.assertEqual(calls[0]["spread_points"], 10)
        self.assertEqual(result["trades"][0]["exit_reason"], "STOP")
        self.assertEqual(result["trades"][0]["entry_spread_points"], 10)
        self.assertEqual(result["trades"][0]["exit_spread_points"], 30)
        self.bars[129]["spread_points"] = 10
        _, changed_calls = self.run_replay(side="SELL", spread_mode="bar_open")
        self.assertEqual(calls[0], changed_calls[0])

    def test_bar_open_spread_fails_closed_when_missing_or_invalid(self):
        with self.assertRaises(ValueError):
            self.run_replay(spread_mode="bar_open")
        for bar in self.bars:
            bar["spread_points"] = 10
        self.bars[129]["spread_points"] = float("nan")
        with self.assertRaises(ValueError):
            self.run_replay(spread_mode="bar_open")

    def test_signals_never_receive_future_or_incomplete_candles(self):
        context = {"M5": [{"time": self.bars[127]["time"]+i*300} for i in range(20)]}
        _, calls = self.run_replay(context=context)
        for market in calls:
            self.assertLess(market["bars"][-1]["time"], market["decision_time"])
            self.assertTrue(all(bar["time"]+300 <= market["decision_time"] for bar in market["context_bars"]["M5"]))

    def test_weekly_replay_passes_only_the_previous_completed_week(self):
        from ramon_pipeline.signalflow.trade_planning import previous_week_bounds
        cutoff = self.bars[128]["time"]
        start, end = previous_week_bounds(cutoff)
        weekly = [{"time": start-900}, {"time": start}, {"time": end-900}, {"time": end}]
        _, calls = self.run_replay(context={"WEEK_M15":weekly}, weekly=True)
        self.assertEqual([b["time"] for b in calls[0]["context_bars"]["WEEK_M15"]], [start, end-900])

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

    def test_holdout_never_evaluates_prior_year_entries(self):
        start = self.bars[130]["time"]
        result, calls = self.run_replay(evaluation_start_time=start)
        self.assertEqual(len(result["trades"]), 1)
        self.assertEqual(result["trades"][0]["entry_time"], start)
        self.assertTrue(all(market["decision_time"] >= start for market in calls))

    def test_exclusive_year_end_prevents_next_year_trade(self):
        end = self.bars[128]["time"]
        result, calls = self.run_replay(evaluation_end_time=end)
        self.assertEqual(result["trade_count"], 0)
        self.assertEqual(len(calls), 0)

    def test_invalid_evaluation_bounds_are_rejected(self):
        with self.assertRaises(ValueError):
            self.run_replay(evaluation_start_time=self.bars[130]["time"],
                            evaluation_end_time=self.bars[129]["time"])
