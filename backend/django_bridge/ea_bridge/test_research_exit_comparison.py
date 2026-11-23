"""No database or MT5 required: deterministic safety tests for research exits."""
import unittest

from .research_exit_comparison import replay_exit, compare_exits


def candles(prices):
    return [{"time": 900 * (i + 1), "open": p[0], "high": p[1],
             "low": p[2], "close": p[3]} for i, p in enumerate(prices)]


class ResearchExitTests(unittest.TestCase):
    def setUp(self):
        self.trade = {"side": "BUY", "opened_at": 900, "entry": 100,
                      "stop": 98, "target": 108, "volume": 1}
        self.costs = dict(spread=0, slippage=0, price_per_lot=1,
                          commission_per_lot=0)

    def test_stop_first_if_both_levels_within_candle(self):
        result = replay_exit(candles([(100, 109, 97, 104)]),
                             self.trade, "FIXED", **self.costs)
        self.assertEqual(result["reason"], "STOP")
        self.assertEqual(result["pnl"], -2)

    def test_breakeven_only_changes_stop_after_candle_closes(self):
        bars = candles([(100, 103, 99, 103), (103, 104, 99.5, 100),
                        (100, 100.5, 99, 100)])
        fixed = replay_exit(bars, self.trade, "FIXED", **self.costs)
        be = replay_exit(bars, self.trade, "BREAKEVEN_1R", **self.costs)
        self.assertEqual(fixed["status"], "OPEN_AT_END")
        self.assertEqual(be["reason"], "STOP")
        self.assertEqual(be["pnl"], 0)
        self.assertEqual(be["modifications"], 1)

    def test_no_stop_widening_after_reversal(self):
        bars = candles([(100, 104, 99, 103), (103, 103, 100.5, 101),
                        (101, 102, 99.5, 100)])
        result = replay_exit(bars, self.trade, "BREAKEVEN_1R", **self.costs)
        self.assertEqual(result["final_stop"], 100)

    def test_gap_excludes_unknown_market_path(self):
        bars = candles([(100, 102, 99, 101), (102, 103, 99, 100)])
        bars[1]["time"] += 900
        result = replay_exit(bars, self.trade, "HYBRID", **self.costs)
        self.assertEqual(result["status"], "DATA_GAP")

    def test_adverse_opening_gap_uses_worse_fill(self):
        result = replay_exit(candles([(97, 99, 96, 98)]),
                             self.trade, "FIXED", **self.costs)
        self.assertEqual(result["exit"], 97)
        self.assertEqual(result["pnl"], -3)

    def test_comparison_is_conditional_and_no_live_actions(self):
        result = compare_exits(candles([(100, 108, 97, 105)]),
                               [self.trade], dict(spread_points=0, point=1,
                               slippage_points=0, tick_value=1, tick_size=1))
        self.assertEqual(result["mode"], "CONDITIONAL_ENTRY_EXIT_RESEARCH_ONLY")
        self.assertEqual(set(result["variants"]), {
            "FIXED", "BREAKEVEN_1R", "ATR_TRAIL", "HYBRID"})
        self.assertEqual(result["variants"]["FIXED"]["closed"], 1)


if __name__ == "__main__":
    unittest.main()
