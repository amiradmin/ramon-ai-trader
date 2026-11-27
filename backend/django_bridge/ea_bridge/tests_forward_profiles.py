import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from django.test import SimpleTestCase, RequestFactory
from django.utils import timezone
from .forward_profiles import new_state, cycle, paper_entry, observe_position, metrics, PROFILES
from . import forward_profiles_views

class ForwardProfilesTests(SimpleTestCase):
    def setUp(self):
        self.config=dict(balance=1024.6,point=.01,tick_size=.01,tick_value=1.,volume_min=.01,volume_step=.01,volume_max=100.,max_spread_points=80.,slippage_points=5.,commission_per_lot=5.,stop_level_points=0,symbol="XAUUSD_l",source="test")
        self.state=new_state(self.config,timezone.now().isoformat())
        self.book=self.state["books"]["trend_baseline"]
        self.quote=dict(time=1000.,bid=100.,ask=100.42)
        self.result=dict(action="BUY",stages={"risk_management":dict(stop_price=98.,target_price=104.,volume=.01)})
    def test_independent_books(self):
        self.assertEqual(len(self.state["books"]),6)
        self.book["balance"]=900
        self.assertEqual(self.state["books"]["trend_utc06_12"]["balance"],1024.6)
        self.assertIs(self.state["affects_orders"],False)
    def test_entry_and_target(self):
        self.assertEqual(paper_entry(self.book,self.result,self.quote,self.config,.5),"PAPER_OPEN")
        observe_position(self.book,dict(time=1010.,bid=104.,ask=104.42),self.config)
        self.assertIsNone(self.book["position"])
        self.assertAlmostEqual(self.book["trades"][0]["pnl"],3.43)
        self.assertEqual(self.book["trades"][0]["reason"],"TARGET")
    def test_adverse_stop_gap(self):
        paper_entry(self.book,self.result,self.quote,self.config,.5)
        observe_position(self.book,dict(time=1010.,bid=97.,ask=97.42),self.config)
        self.assertAlmostEqual(self.book["trades"][0]["exit"],96.95)
        self.assertLess(self.book["trades"][0]["pnl"],0)
    def test_budget(self):
        self.result["stages"]["risk_management"]["volume"]=.1
        self.assertEqual(paper_entry(self.book,self.result,self.quote,self.config,.5),"PAPER_FILL_RISK_OR_REWARD_REJECTED")
        self.assertIsNone(self.book["position"])
    def test_duplicate_quote(self):
        self.assertEqual(cycle(self.state,[],self.quote,1000),"WARMUP")
        self.assertEqual(cycle(self.state,[],self.quote,1001),"WAIT_NEW_QUOTE")
        self.assertEqual(self.state["quote_samples"],1)
    def test_exposure_gap(self):
        paper_entry(self.book,self.result,self.quote,self.config,.5)
        cycle(self.state,[],self.quote,1000)
        cycle(self.state,[],dict(time=1040.,bid=100.,ask=100.42),1040)
        self.assertTrue(self.state["exposure_gap"])
        self.assertEqual(self.state["missed_quote_intervals"],1)
    def test_metrics_empty(self):
        metrics(self.state)
        self.assertIsNone(self.book["metrics"]["profit_factor"])
        self.assertEqual(self.state["comparisons"]["trend_utc06_12"],0)
    def test_report_download(self):
        self.state["updated_at"]=timezone.now().isoformat()
        metrics(self.state)
        with TemporaryDirectory() as directory:
            path=Path(directory)/"state.json"
            path.write_text(json.dumps(self.state))
            req=RequestFactory().get("/report",{"download":"1"})
            req.user=SimpleNamespace(is_active=True,is_staff=True)
            with patch.object(forward_profiles_views,"STATE",path):
                response=forward_profiles_views.report(req)
            self.assertEqual(response.status_code,200)
            self.assertIn("forward-report-v15.3.json",response["Content-Disposition"])
            self.assertTrue(json.loads(response.content)["report_fresh"])
    def test_missing_report(self):
        req=RequestFactory().get("/report")
        req.user=SimpleNamespace(is_active=True,is_staff=True)
        with TemporaryDirectory() as directory:
            with patch.object(forward_profiles_views,"STATE",Path(directory)/"missing.json"):
                self.assertEqual(forward_profiles_views.report(req).status_code,503)
    def test_post_disallowed(self):
        req=RequestFactory().post("/report")
        req.user=SimpleNamespace(is_active=True,is_staff=True)
        self.assertEqual(forward_profiles_views.report(req).status_code,405)
