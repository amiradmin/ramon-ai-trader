from django.test import SimpleTestCase
from .entry_quality_research import bucket, features, rejected_volume, summarize_entries

class EntryQualityResearchTests(SimpleTestCase):
    def setUp(self):
        self.market=dict(latest_bar_time=0,ask=100.1,bid=100,point=.01,point_value_per_lot=1,target_reward_risk=1.25)
        self.result=dict(stages=dict(opportunities=dict(approved=True,side="BUY",score=.75,setup={"atr":1},regime={"label":"BULLISH"}),
            entry_timing=dict(approved=True,quality=.8),risk_management=dict(reason="RISK_BUDGET_BELOW_MINIMUM_VOLUME",stop_price=99,stop_points=110)))
        self.future=[dict(time=900,open=100,low=99.8,high=102,close=101)]
    def audit(self):
        return rejected_volume(self.result,self.market,self.future,slippage_points=0,commission_per_lot=5)
    def test_features(self):
        f=features(self.result,self.market)
        self.assertEqual(f["score"],.75)
        self.assertAlmostEqual(f["stop_atr"],1.1)
        self.assertEqual(f["quality"],.8)
    def test_bucket(self):
        self.assertEqual(bucket(None,(.55,.65)),"UNKNOWN")
        self.assertEqual(bucket(.65,(.55,.65)),">=0.65")
    def test_cost(self):
        r=self.audit()
        self.assertEqual(r["outcome"],"TARGET")
        self.assertLess(r["net_r"],r["gross_r"])
    def test_other_rejection(self):
        self.result["stages"]["risk_management"]["reason"]="TARGET_ROOM_TOO_SMALL"
        self.assertIsNone(self.audit())
    def test_gap(self):
        self.future[0]["time"]=1800
        r=self.audit()
        self.assertEqual(r["outcome"],"DATA_GAP")
        self.assertNotIn("net_r",r)
    def test_summary(self):
        r=self.audit()
        s=summarize_entries([dict(entry_features=features(self.result,self.market),pnl=2)],[r])
        self.assertEqual(s["rejected_volume"]["resolved"],1)
        self.assertEqual(s["rejected_volume"]["observations"],1)
        self.assertEqual(len(s["actual_buckets"]),6)
    def test_empty(self):
        self.assertIsNone(summarize_entries([],[])["rejected_volume"]["mean_net_r_resolved_only"])
