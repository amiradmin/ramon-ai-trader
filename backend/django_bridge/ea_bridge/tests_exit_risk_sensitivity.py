from django.test import SimpleTestCase
from .management.commands.evaluate_exit_risk_sensitivity import summarize, PLAN

def row(pnl=4, trades=2, wins=1, delta=1):
    return dict(delta_vs_baseline=delta,data_quality={"validation_status":"DESCRIPTIVE_ONLY_HISTORY_QUALITY_WARNING"},metrics=dict(
        closed_trades=trades,wins=wins,net_gain=pnl,max_drawdown_pct=2,open_position=False,evaluations=100,
        trade_diagnostics={"by_exit":[{"gross_profit":6,"gross_loss":-2}]},
        rejections=[{"reason":"RISK_BUDGET_BELOW_MINIMUM_VOLUME","count":7},{"reason":"OTHER","count":9}]))

class ExitRiskSensitivityTests(SimpleTestCase):
    def test_plan(self):
        self.assertEqual(sum(map(len,PLAN.values())),8)
        self.assertTrue(all(p[0]=="baseline" for p in PLAN.values()))
    def test_aggregate(self):
        s=summarize([row(),row()],30)
        self.assertEqual(s["closed_trades"],4)
        self.assertEqual(s["net_pnl"],8)
        self.assertEqual(s["pooled_profit_factor"],3)
        self.assertEqual(s["win_rate"],50)
        self.assertEqual(s["minimum_lot_rejections"],14)
        self.assertEqual(s["windows_meeting_minimum"],0)
        self.assertEqual(s["history_warning_windows"],2)
    def test_missing_run(self):
        s=summarize([row(delta=None),{"error":"missing"}],1)
        self.assertEqual(s["windows"],2)
        self.assertEqual(s["successful_windows"],1)
        self.assertEqual(s["paired_windows"],0)
        self.assertIsNone(s["paired_delta"])
    def test_empty(self):
        s=summarize([],30)
        self.assertIsNone(s["net_pnl"])
        self.assertIsNone(s["pooled_profit_factor"])
        self.assertIsNone(s["win_rate"])
        self.assertIsNone(s["worst_window_closed_drawdown_pct"])
