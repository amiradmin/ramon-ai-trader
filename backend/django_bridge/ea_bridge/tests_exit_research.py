from django.test import SimpleTestCase
from .exit_research import POLICIES, next_stop, close_signal

class ExitResearchTests(SimpleTestCase):
    def setUp(self):
        self.pos=dict(side="BUY",entry=100,initial_stop=99,stop=99,target=105,held_bars=8)
        self.bar=dict(close=102)
        self.prev=dict(low=101,high=103)
    def test_baseline(self):
        self.assertEqual(next_stop(self.pos,102,.5,.01,POLICIES["baseline"]),99)
        self.assertIsNone(close_signal(self.pos,self.bar,self.prev,102,POLICIES["baseline"]))
    def test_breakeven_trigger(self):
        self.assertEqual(next_stop(self.pos,100.4,.5,.01,POLICIES["be_0.5"]),99)
        self.assertEqual(next_stop(self.pos,100.5,.5,.01,POLICIES["be_0.5"]),100)
    def test_trailing(self):
        self.assertAlmostEqual(next_stop(self.pos,102,.5,.01,POLICIES["trail_1.5"]),101.25)
    def test_sell_trailing(self):
        self.pos.update(side="SELL",initial_stop=101,stop=101,target=95)
        self.assertAlmostEqual(next_stop(self.pos,98,.5,.01,POLICIES["trail_1.5"]),98.75)
    def test_no_widen(self):
        self.pos["stop"]=101.8
        self.assertEqual(next_stop(self.pos,102,.5,.01,POLICIES["be_trail"]),101.8)
    def test_missing_atr(self):
        self.assertEqual(next_stop(self.pos,102,None,.01,POLICIES["trail_1"]),99)
    def test_time_exit(self):
        self.assertEqual(close_signal(self.pos,self.bar,self.prev,102,POLICIES["time_8"]),"TIME_EXIT")
        self.assertIsNone(close_signal(self.pos,self.bar,self.prev,102,POLICIES["time_16"]))
    def test_reversal(self):
        self.assertEqual(close_signal(self.pos,dict(close=100),self.prev,100,POLICIES["reversal"]),"REVERSAL_EXIT")
        self.assertIsNone(close_signal(self.pos,self.bar,self.prev,102,POLICIES["reversal"]))
    def test_remaining_rr(self):
        self.assertEqual(close_signal(self.pos,dict(close=104),self.prev,104,POLICIES["low_remaining_rr"]),"LOW_REMAINING_RR_EXIT")
        self.assertIsNone(close_signal(self.pos,self.bar,self.prev,99.5,POLICIES["low_remaining_rr"]))
