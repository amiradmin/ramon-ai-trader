from datetime import datetime,timedelta,timezone
from django.test import SimpleTestCase
from .execution_quality import price_metrics,seconds,summary

class ExecutionQualityTests(SimpleTestCase):
    def setUp(self):
        self.market=dict(bid=100,ask=100.42,point=.01)

    def test_buy_price_decomposition(self):
        m=price_metrics("BUY",self.market,dict(request_price=104.20,request_fill_price=103.66))
        self.assertAlmostEqual(m["decision_to_request_points"],378)
        self.assertAlmostEqual(m["request_slippage_points"],-54)
        self.assertAlmostEqual(m["decision_to_fill_points"],324)
        self.assertEqual(m["hypothetical_warning_thresholds"],[.5,1,2,3])

    def test_sell_signed_deviation(self):
        m=price_metrics("SELL",self.market,dict(request_price=99.5,request_fill_price=99.6))
        self.assertAlmostEqual(m["decision_to_request_points"],50)
        self.assertAlmostEqual(m["request_slippage_points"],-10)
        self.assertAlmostEqual(m["decision_to_fill_points"],40)

    def test_favorable_request_has_no_warning(self):
        m=price_metrics("BUY",self.market,dict(request_price=100,request_fill_price=100))
        self.assertEqual(m["hypothetical_warning_thresholds"],[])

    def test_exact_threshold(self):
        m=price_metrics("BUY",self.market,dict(request_price=100.84))
        self.assertEqual(m["hypothetical_warning_thresholds"],[.5])

    def test_missing_price_stays_missing(self):
        m=price_metrics("BUY",self.market,{})
        self.assertIsNone(m["decision_to_fill_points"])
        self.assertIsNone(m["request_slippage_points"])

    def test_unknown_side(self):
        m=price_metrics("HOLD",self.market,dict(request_price=101,request_fill_price=102))
        self.assertIsNone(m["decision_to_request_points"])
        self.assertEqual(m["hypothetical_warning_thresholds"],[])

    def test_zero_spread_no_ratio(self):
        m=price_metrics("BUY",dict(bid=100,ask=100,point=.01),dict(request_price=101))
        self.assertIsNone(m["adverse_request_spread_ratio"])

    def test_timestamp_intervals(self):
        a=datetime(2026,11,26,tzinfo=timezone.utc)
        self.assertEqual(seconds(a,a+timedelta(seconds=2)),2)
        self.assertIsNone(seconds(a,a-timedelta(seconds=1)))
        self.assertIsNone(seconds(None,a))

    def test_summary_and_coverage(self):
        r=summary([{"v":1},{"v":3},{"v":None},{"v":2}],"v")
        self.assertEqual(r["mean"],2)
        self.assertEqual(r["p95"],3)
        self.assertEqual(r["missing"],1)
        self.assertIsNone(summary([],"v")["mean"])
