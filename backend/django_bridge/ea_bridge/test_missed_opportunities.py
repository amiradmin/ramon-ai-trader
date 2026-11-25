from django.test import SimpleTestCase
from .missed_opportunities import audit_rejection, summarize_audit
from .management.commands.evaluate_trend_pullback import V5


class MissedOpportunityTests(SimpleTestCase):
    def setUp(self):
        self.market = dict(latest_bar_time=900, ask=100.1, bid=100,
                           point=0.1, target_reward_risk=1.25)
        self.result = {"stages": {
            "opportunities": {"approved": True, "side": "BUY"},
            "entry_timing": {"approved": True},
            "risk_management": {"approved": False,
                "reason": "RISK_BUDGET_BELOW_MINIMUM_VOLUME", "stop_price": 99.1}}}

    def test_next_open_and_stop_first(self):
        row = audit_rejection(self.result, self.market,
                             [dict(time=1800, open=100, high=102, low=98)])
        self.assertEqual(row["outcome"], "STOP")
        self.assertAlmostEqual(row["hypothetical_entry"], 100.1)
        self.assertEqual(summarize_audit([row])["groups"][row["reason"]]["outcomes"], {"STOP": 1})

    def test_gap_is_not_a_fill(self):
        row = audit_rejection(self.result, self.market,
                             [dict(time=2700, open=100, high=102, low=99)])
        self.assertEqual(row["outcome"], "DATA_GAP")

    def test_upstream_rejection_is_not_a_trade_plan(self):
        self.result["stages"]["entry_timing"]["approved"] = False
        self.assertIsNone(audit_rejection(self.result, self.market, []))

    def test_v5_baseline_and_session_candidate(self):
        self.assertEqual(len(V5), 9)
        self.assertEqual(V5["baseline"], {})
        self.assertEqual(V5["utc_06_24"]["entry_hours"], [6,24])
