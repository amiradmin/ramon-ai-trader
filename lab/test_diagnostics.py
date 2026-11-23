"""Regression coverage for laboratory diagnostic reports; no database or MT5."""
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from diagnostics import (gap_diagnostics, risk_diagnostics, contract_diagnostics,
                         per_year, per_regime)
from test_impulse_research import candles, evaluate


class DiagnosticTests(unittest.TestCase):
    def test_expected_weekend_and_intraday_gaps_separate(self):
        # 2026-11-20 21:45 UTC Fri -> Nov 22 Sunday then an intraday hole
        start=int(datetime(2026,11,20,21,45,tzinfo=timezone.utc).timestamp())
        end=int(datetime(2026,11,22,22,0,tzinfo=timezone.utc).timestamp())
        rows=[{"time":start},{"time":end},{"time":end+2700}]
        report=gap_diagnostics(rows)
        self.assertEqual(report["total_discontinuities"],2)
        self.assertEqual(report["categories"]["LIKELY_WEEKEND_CLOSURE"],1)
        self.assertEqual(report["categories"]["UNEXPECTED_INTRADAY_GAP"],1)
        self.assertFalse(report["verified_market_calendar"])

    def test_minimum_lot_risk_rejection_is_quantified(self):
        report={"risk_rejections":[{
            "minimum_lot_risk":12.0,
            "minimum_lot_risk_percent":1.2,
            "minimum_required_balance":2400,
            "stop_points":1200,
            "affordable_volume_unrounded":.004,
        }]}
        d=risk_diagnostics(report)
        self.assertEqual(d["count"],1)
        self.assertEqual(d["summary"]["minimum_lot_risk"]["median"],12)
        self.assertEqual(d["summary"]["minimum_required_balance"]["max"],2400)

    def test_contract_is_never_marked_verified(self):
        args=SimpleNamespace(balance=1024.23,point=.01,
            tick_size=.01,tick_value=1,volume_min=.01,
            volume_max=100,volume_step=.01)
        c=contract_diagnostics(args)
        self.assertFalse(c["verified"])
        self.assertEqual(c["status"],"UNVERIFIED_BROKER_CONTRACT")

    def test_impulse_risk_rejection_records_are_populated(self):
        # Synthetic breakout data can produce a signal; large tick value
        # ensures min-lot sizing is unaffordable without bypassing policy.
        bars=[]
        start=1800000000-1800000000%900
        for i in range(100):
            value=3000+(i*.17)
            bars.append({"time":start+900*i,"open":value,
                         "high":value+.55,"low":value-.35,
                         "close":value+.42})
        result=evaluate(bars,balance=1000,tick_value=200,
                        spread_points=1,slippage_points=0)
        self.assertFalse(result["executions_authorized"])
        self.assertEqual(len(result["risk_rejections"]),
                         result["rejections"].get("MINIMUM_LOT_EXCEEDS_RISK",0))
        for row in result["risk_rejections"]:
            self.assertGreater(row["minimum_required_balance"],1000)

    def test_year_regime_are_based_on_existing_labels_only(self):
        stamp=int(datetime(2025,5,4,tzinfo=timezone.utc).timestamp())
        report={"trades":[{"opened_at":stamp,"closed_at":stamp+900,
                            "pnl":4,"signal_regime":"RANGE"},
                          {"opened_at":stamp+1800,"closed_at":stamp+2700,
                            "pnl":-1}]}
        self.assertEqual(per_year(report,initial_balance=1000)["2025"]["closed_trades"],2)
        values=per_regime(report,initial_balance=1000)
        self.assertEqual(values["RANGE"]["net_gain"],4)
        self.assertEqual(values["UNCLASSIFIED"]["net_gain"],-1)


if __name__=="__main__":
    unittest.main()
