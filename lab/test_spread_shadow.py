"""Offline safety and cohort regression tests for isolated spread experiment."""
from datetime import date, datetime, timezone
from types import SimpleNamespace
import unittest

from spread_shadow import run_shadow, cohort_difference, comparison_report
from render_spread_shadow import render

T=lambda x:int(datetime(2025,2,x,tzinfo=timezone.utc).timestamp())


def trade(signal, date_day, pnl=1, side="BUY", gaps=0):
    when=T(date_day)
    return {"signal_at":signal,"side":side,"opened_at":when,"closed_at":when+900,
            "pnl":pnl,"gaps_crossed":gaps}


class DummyReplay:
    def __init__(self):
        self.pipeline=SimpleNamespace(
            entry_timer=SimpleNamespace(maximum_spread_points=50.0),
            risk_manager=SimpleNamespace(maximum_risk_percent=.5))
        self._research_pipeline=lambda *args,**kwargs:self.pipeline
        self._simulate=self.fake_simulate
        self.saw=[]

    def fake_simulate(self,data,rows,progress=None):
        pipe=self._research_pipeline("TREND_PULLBACK")
        self.saw.append((data["spread_points"],
                         pipe.entry_timer.maximum_spread_points,
                         pipe.risk_manager.maximum_risk_percent))
        if progress:
            progress(1,1)
        return [{"trades":[],"signals":0,"closed_trades":0,"net_gain":0,
                 "rejections":[]}]


class ShadowSafetyTests(unittest.TestCase):
    def setUp(self):
        self.replay=DummyReplay()
        self.inputs={"spread_points":42.0,"risk_percent":.5,"balance":1024.23,
                     "slippage_points":2.0}

    def test_research_spread_limit_restored(self):
        run_shadow(self.replay,self.inputs,[],spread_points=84,limit_points=90)
        self.assertEqual(self.replay.saw,[(84,90,.5)])
        self.assertEqual(self.replay.pipeline.entry_timer.maximum_spread_points,90.0)
        # Only the dummy pipeline object changed; no production config touched.
        self.assertEqual(self.inputs["spread_points"],42.0)

    def test_db_configured_limit_above_50_is_not_a_live_guard_failure(self):
        self.replay.pipeline.entry_timer.maximum_spread_points=75.0
        run_shadow(self.replay,self.inputs,[],spread_points=84,limit_points=90)
        self.assertEqual(self.replay.saw,[(84,90,.5)])

    def test_fixed_50_control_is_explicit_not_assumed_from_loaded_threshold(self):
        self.replay.pipeline.entry_timer.maximum_spread_points=75.0
        report=comparison_report(
            replay_module=self.replay,data=self.inputs,rows=[],
            start=date(2025,1,1),end=date(2025,3,1),
            baseline={"trades":[],"signals":0,"closed_trades":0,
                      "net_gain":0,"rejections":[]},quality={"gap_count":0})
        self.assertEqual(report["configured_entry_spread_limit_points"],75.0)
        self.assertEqual(report["scenarios"][0]["absolute_spread_limit_points"],50.0)
        self.assertEqual(self.replay.saw[0],(42,50,.5))
        self.assertFalse(report["live_eligible"])

    def test_reject_unapproved_limits_and_risk(self):
        for limit in (0,500,200):
            with self.assertRaisesRegex(ValueError,"fixed presets"):
                run_shadow(self.replay,self.inputs,[],spread_points=84,limit_points=limit)
        with self.assertRaisesRegex(ValueError,"Risk cap"):
            run_shadow(self.replay,{**self.inputs,"risk_percent":1.0},[],
                       spread_points=84,limit_points=90)

    def test_cohorts_do_not_count_shared_signals(self):
        old={"trades":[trade(1,3,pnl=4),trade(2,4,pnl=-1)]}
        new={"trades":[trade(1,3,pnl=3),trade(3,5,pnl=-2,gaps=1),
                       trade(4,6,pnl=1)]}
        c=cohort_difference(old,new,1024.23,date(2025,1,1),date(2025,3,1))
        self.assertEqual(c["newly_admitted"]["closed_trades"],2)
        self.assertEqual(c["displaced_control_trades"]["closed_trades"],1)
        self.assertEqual(c["newly_admitted_gap_unaffected"]["closed_trades"],1)
        self.assertEqual(c["new_trades_by_entry_partition"][0]["closed_trades"],2)

    def test_reject_unsupported_baseline(self):
        with self.assertRaisesRegex(ValueError,"42-point baseline"):
            comparison_report(replay_module=self.replay,
                data={**self.inputs,"spread_points":45},rows=[],
                start=date(2025,1,1),end=date(2025,3,1),
                baseline={"trades":[]},quality={})

    def test_renderer_rejects_partial_results(self):
        with self.assertRaisesRegex(ValueError,"Incomplete"):
            render({"mode":"SPREAD_SHADOW_READ_ONLY_RESEARCH",
                    "scenarios":[],"matched_spread_control_comparisons":[]})


if __name__=="__main__":
    unittest.main()
