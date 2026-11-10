import unittest
from compare import resolve

class ExitExecutionTests(unittest.TestCase):
    def setUp(self):
        self.t=dict(side='BUY',entry=100.05,stop=90,target=112.5,entry_time=900)
        self.b=[dict(time=900+i*900,open=100,high=100.5,low=99.5,close=100) for i in range(24)]
        self.a=dict(point=.01)

    def test_protection_cannot_stop_out_on_trigger_candle(self):
        self.b[0].update(high=111,low=99,close=110)
        self.b[1].update(open=110,high=111,low=99,close=100)
        row=resolve(self.t,self.b,self.a,'breakeven_1R')
        self.assertEqual(row['reason'],'STOP')
        self.assertAlmostEqual(row['net_r'],-.005)

    def test_stop_wins_over_trigger_and_target(self):
        self.b[0].update(high=114,low=89)
        self.assertEqual(resolve(self.t,self.b,self.a,'breakeven_1R')['reason'],'STOP')
        self.assertLess(resolve(self.t,self.b,self.a,'partial_1R')['net_r'],-1)

    def test_partial_is_accounted_for_before_target_in_same_bar(self):
        self.b[0].update(high=114)
        self.assertAlmostEqual(resolve(self.t,self.b,self.a,'partial_1R')['net_r'],1.115)

    def test_gapped_windows_are_excluded(self):
        self.b[2]['time']+=900
        self.assertIsNone(resolve(self.t,self.b,self.a,'current'))

    def test_short_stop_uses_ask_and_gap_is_adverse(self):
        self.t.update(side='SELL',entry=99.95,stop=110,target=87.5)
        self.b[0].update(high=109.8)
        self.assertEqual(resolve(self.t,self.b,self.a,'current')['reason'],'STOP')
        self.t.update(side='BUY',entry=100.05,stop=90,target=112.5)
        self.b[0].update(open=85,low=84,high=86,close=85)
        self.assertLess(resolve(self.t,self.b,self.a,'current')['net_r'],-1.5)

if __name__=='__main__': unittest.main()
