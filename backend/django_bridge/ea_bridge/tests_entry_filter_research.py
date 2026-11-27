from django.test import SimpleTestCase
from .entry_filter_research import PLAN, validate_filter, filter_reason
from .management.commands.evaluate_entry_filters import summarize

class EntryFilterResearchTests(SimpleTestCase):
    def setUp(self):
        self.market=dict(latest_bar_time=6*3600-900,point=.01)
        self.result=dict(action="BUY",stages=dict(opportunities=dict(side="BUY",score=.85,setup={"atr":1},regime={"label":"RANGING"}),
            entry_timing=dict(quality=.8),risk_management=dict(stop_points=200)))
    def reason(self,name):return filter_reason(name,self.result,self.market)
    def test_plan(self):
        self.assertEqual(sum(len(p[1])-1 for p in PLAN.values()),9)
    def test_baseline(self):
        self.assertIsNone(filter_reason("baseline",{},{}))
    def test_score(self):
        self.assertIsNone(self.reason("score85"))
        self.result["stages"]["opportunities"]["score"]=.84
        self.assertEqual(self.reason("score85"),"V15_FILTER_SCORE")
        self.assertIsNone(self.reason("score75"))
    def test_time(self):
        self.assertIsNone(self.reason("score85_utc06_12"))
        self.market["latest_bar_time"]=12*3600-900
        self.assertEqual(self.reason("utc06_12"),"V15_FILTER_UTC")
        self.assertIsNone(self.reason("utc06_18"))
    def test_stop(self):
        self.assertIsNone(self.reason("stop2_25"))
        self.result["stages"]["risk_management"]["stop_points"]=250
        self.assertEqual(self.reason("stop2_25"),"V15_FILTER_STOP_ATR")
    def test_regime(self):
        self.assertIsNone(self.reason("ranging"))
        self.result["stages"]["opportunities"]["regime"]={"label":"BULLISH"}
        self.assertEqual(self.reason("ranging"),"V15_FILTER_REGIME")
    def test_side(self):
        self.assertIsNone(self.reason("buy_utc06_18"))
        self.result["action"]="SELL"
        self.assertEqual(self.reason("buy_only"),"V15_FILTER_SIDE")
    def test_missing(self):
        del self.result["stages"]["opportunities"]["score"]
        self.assertEqual(self.reason("score85"),"V15_FILTER_SCORE_MISSING")
    def test_isolation(self):
        data=dict(setups=["TREND_PULLBACK"],all_strategy_risk_research=True,v15_entry_filter="score85")
        self.assertEqual(validate_filter(data,"M15","baseline"),"score85")
        for tf,exit_name in [("M5","baseline"),("M15","time_8")]:
            with self.assertRaises(ValueError):validate_filter(data,tf,exit_name)
        with self.assertRaises(ValueError):validate_filter(dict(data,setups=["SESSION_OPENING_RANGE_BREAKOUT"]),"M15","baseline")
        self.assertIsNone(validate_filter({},"M15",None))
    def test_empty_summary(self):
        s=summarize([{"error":"missing"}])
        self.assertEqual(s["successful_windows"],0)
        self.assertIsNone(s["net_pnl"])
        self.assertIsNone(s["paired_delta"])
