"""Standalone tests: partitioning is stable and dashboard is offline and escaped."""
import unittest
from datetime import date, datetime, timezone
from segmented import partition_dates, period_metrics, breakdown, fingerprint, quality_gate
from render_report import render


def stamp(y,m,d):
    return int(datetime(y,m,d,tzinfo=timezone.utc).timestamp())


class ChronologicalLabTests(unittest.TestCase):
    def test_fixed_partitions_are_disjoint_and_ordered(self):
        parts=partition_dates(date(2024,1,24),date(2026,11,20))
        self.assertEqual([x["name"] for x in parts],
                         ["TRAIN","VALIDATION","TEST_UNTOUCHED"])
        self.assertEqual(parts[0]["end"],"2025-06-30")
        self.assertEqual(parts[1]["start"],"2025-07-01")
        self.assertEqual(parts[1]["end"],"2026-03-31")
        self.assertEqual(parts[2]["start"],"2026-04-01")

    def test_trade_split_by_entry_and_boundary_is_flagged(self):
        data={"trades":[
            {"opened_at":stamp(2025,6,30),"closed_at":stamp(2025,7,1),
             "pnl":10.0,"reason":"TARGET"},
            {"opened_at":stamp(2026,4,2),"closed_at":stamp(2026,4,2),
             "pnl":-5.0,"reason":"STOP"},
        ]}
        parts=breakdown(data,date(2024,1,24),date(2026,11,20),1000,name="test")
        train, val, test=parts["periods"]
        self.assertEqual(train["closed_trades"],1)
        self.assertEqual(train["boundary_crossing_trades"],1)
        self.assertEqual(val["closed_trades"],0)
        self.assertEqual(test["closed_trades"],1)
        self.assertEqual(test["net_gain"],-5)

    def test_missing_losses_does_not_claim_infinite_pf(self):
        p=period_metrics([{"opened_at":1,"closed_at":2,"pnl":1}],1000)
        self.assertIsNone(p["profit_factor"])
        self.assertEqual(p["expectancy_per_trade"],1.0)

    def test_stable_history_fingerprint(self):
        data=[{"time":1,"open":2.,"high":3.,"low":1.,"close":2.}]
        self.assertEqual(fingerprint(data), fingerprint(list(data)))
        data[0]["close"]=2.1
        self.assertNotEqual(fingerprint(data),
                            fingerprint([{"time":1,"open":2.,"high":3.,"low":1.,"close":2.}]))

    def test_quality_gate_never_approves_live(self):
        gate=quality_gate(
            {"gap_count":3,"excluded":{"off_grid":1}},
            {"periods":[]},
            {"status":"DESCRIPTIVE_ONLY","periods":[
                {"name":"TEST_UNTOUCHED","closed_trades":100}]})
        self.assertFalse(gate["live_eligible"])
        self.assertIn("GAPS_IN_STORED_M15_HISTORY",gate["warnings"])

    def test_dashboard_escapes_untrusted_dataset_labels(self):
        row={"name":"TRAIN","start":"2024-01-01","end":"2025-01-01",
             "closed_trades":1,"win_rate":50,"net_gain":2,
             "profit_factor":1.2,"expectancy_per_trade":2,
             "max_closed_trade_drawdown_pct":1,"gap_affected_trades":0}
        payload={"mode":"ISOLATED_READ_ONLY_LABORATORY",
            "market_source":"<script>alert(1)</script>",
            "symbol":"XAUUSD_l",
            "start":"2024-01-01","end":"2026-11-20",
            "data_quality":{"gap_count":0},
            "historical_period_comparisons":[
                {"name":"TREND_PULLBACK","periods":[row]},
                {"name":"IMPULSE_CONTINUATION","periods":[row]}],
            "trend_pullback":{"closed_trades":1,"net_gain":2,"win_rate":50,"profit_factor":1.2},
            "impulse_continuation":{"closed_trades":1,"net_gain":2,"win_rate":50,"profit_factor":1.2}}
        html=render(payload)
        self.assertNotIn("<script>",html)
        self.assertIn("&lt;script&gt;",html)
        self.assertIn("NO LIVE ORDERS",html)


if __name__=="__main__":
    unittest.main()
