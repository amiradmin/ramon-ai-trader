"""Offline broker contract and cost-filter regression tests."""
import unittest
from diagnostics import sensitivity_metric, gap_diagnostics
from verify_mt5_contract import compare


class BrokerAndCostDiagnosticTests(unittest.TestCase):
    def test_spread_filter_reasons_retained_with_zero_trades(self):
        baseline={"signals":0,"closed_trades":0,"net_gain":0.0,
                  "evaluations":100,"rejection_total":100,
                  "rejections":[{"stage":"ENTRY_TIMING",
                                 "reason":"SPREAD_TOO_WIDE",
                                 "count":100}]}
        result=sensitivity_metric(baseline)
        self.assertEqual(result["closed_trades"],0)
        self.assertEqual(result["rejection_total"],100)
        self.assertEqual(result["rejections"][0]["count"],100)

    def test_mt5_snapshot_mismatch_does_not_enable_live(self):
        snapshot={"source":"MT5_CONNECTED_TERMINAL_READ_ONLY",
                  "symbol":"XAUUSD_l","currency":"USC","balance":1100.0,
                  "broker_fields":{
                     "point":.01,"trade_tick_size":.01,"trade_tick_value":.01,
                     "volume_min":.01,"volume_max":100,"volume_step":.01}}
        report={"mode":"ISOLATED_READ_ONLY_LABORATORY","symbol":"XAUUSD_l",
                "assumptions":{"point":.01,"tick_size":.01,"tick_value":1,
                               "volume_min":.01,"volume_max":100,
                               "volume_step":.01,"balance":1024.23}}
        result=compare(snapshot,report)
        self.assertFalse(result["live_eligible"])
        self.assertEqual(result["mismatches"],1)
        self.assertFalse(result["balance_matches_snapshot"])

    def test_refuse_symbol_mismatch(self):
        with self.assertRaisesRegex(ValueError,"Symbol mismatch"):
            compare({"source":"MT5_CONNECTED_TERMINAL_READ_ONLY",
                     "symbol":"XAUUSD_o"},
                    {"mode":"ISOLATED_READ_ONLY_LABORATORY",
                     "symbol":"XAUUSD_l"})

    def test_gap_carries_intraday_hour_pattern(self):
        rows=[{"time":900*1000},{"time":900*1002}]
        data=gap_diagnostics(rows)
        self.assertEqual(data["categories"]["UNEXPECTED_INTRADAY_GAP"],1)
        self.assertEqual(sum(data["unexpected_by_year"].values()),1)
        self.assertEqual(len(data["unexpected_examples"]),1)


if __name__=="__main__":
    unittest.main()
