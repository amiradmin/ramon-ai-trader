from django.test import SimpleTestCase
from ramon_pipeline.signalflow.entry_timing import EntryTimer, _spread_out_of_range


class SpreadBoundaryTests(SimpleTestCase):
    def test_inclusive_limit(self):
        for spread in (79,80,80+1e-10):
            self.assertFalse(_spread_out_of_range(spread,80))
        for spread in (81,80.00001,-1,float("inf"),float("nan")):
            self.assertTrue(_spread_out_of_range(spread,80))

    def test_quote_arithmetic(self):
        timer = EntryTimer(maximum_spread_points=80)
        opportunity = {"approved":True,"side":"BUY","setup":{
            "atr":10,"reference_price":3500,"invalidation_price":3490}}
        for points in (79,80,81):
            result = timer.evaluate(opportunity,{"bid":3500,"ask":3500+points*.01,"point":.01})
            self.assertEqual(result["reason"]=="SPREAD_OUT_OF_RANGE",points>80)

    def test_legacy_boundary(self):
        timer = EntryTimer(maximum_spread_points=80)
        for points in (79,80,81):
            result=timer.evaluate({"approved":True},{"entry_quality":1,"spread_points":points})
            self.assertEqual(result["approved"],points<=80)
