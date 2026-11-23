"""Test offline cost attribution without invoking DB, Django or MT5."""
import unittest
from analyze_trend_costs import analyze


class CostAttributionTests(unittest.TestCase):
    def test_rejection_changes_and_live_guard(self):
        payload={"mode":"ISOLATED_READ_ONLY_LABORATORY",
                 "market_source":"fixture","symbol":"XAUUSD_l",
                 "cost_sensitivity":[
                    {"name":"BASE_COST","spread_points":42,"slippage_points":2,
                     "trend_pullback":{"signals":2,"closed_trades":2,"net_gain":3,
                       "evaluations":10,"rejection_total":8,
                       "rejections":[{"stage":"ENTRY_TIMING","reason":"SPREAD",
                                      "count":2}]}},
                    {"name":"SPREAD_ONLY_2X","spread_points":84,"slippage_points":2,
                     "trend_pullback":{"signals":0,"closed_trades":0,"net_gain":0,
                       "evaluations":10,"rejection_total":10,
                       "rejections":[{"stage":"ENTRY_TIMING","reason":"SPREAD",
                                      "count":4}]}}]}
        result=analyze(payload)
        self.assertFalse(result["live_eligible"])
        comparison=result["comparisons"][1]
        self.assertEqual(comparison["closed_trades"],0)
        self.assertEqual(comparison["top_rejection_changes"][0]["delta"],2)
        self.assertEqual(comparison["top_rejection_changes"][0]["stage"],"ENTRY_TIMING")

    def test_requires_baseline(self):
        with self.assertRaisesRegex(ValueError,"BASE_COST"):
            analyze({"mode":"ISOLATED_READ_ONLY_LABORATORY",
                     "cost_sensitivity":[{"name":"DOUBLE_COST"}]})

    def test_requires_research_mode(self):
        with self.assertRaisesRegex(ValueError,"Invalid"):
            analyze({"mode":"LIVE","cost_sensitivity":[{"name":"BASE_COST"}]})


if __name__=="__main__":
    unittest.main()
