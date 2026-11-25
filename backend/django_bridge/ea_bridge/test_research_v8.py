from datetime import date
from django.test import SimpleTestCase
from .management.commands.evaluate_research_v8 import V8, windows, scenarios, streak


class ResearchV8Tests(SimpleTestCase):
    def test_disjoint_windows_include_last_day(self):
        self.assertEqual(list(windows(date(2026,1,1),date(2026,1,17),7)),
            [(date(2026,1,1),date(2026,1,7)),
             (date(2026,1,8),date(2026,1,14)),
             (date(2026,1,15),date(2026,1,17))])

    def test_one_factor_cost_scenarios(self):
        self.assertEqual(scenarios(False), [(42,1.5)])
        self.assertEqual(set(scenarios(True)),
            {(42,1.5),(30,1.5),(60,1.5),(80,1.5),(42,0.5),(42,1.0)})

    def test_losing_streak(self):
        self.assertEqual(streak([{"pnl":p} for p in (-1,-1,0,-1,1,-1,-1,-1)]),3)
        self.assertEqual(streak([]),0)

    def test_frozen_policies(self):
        self.assertEqual(len(V8),7)
        self.assertEqual(V8["m5_sell_both"]["confirm_timeframes"],["M15","H1"])
        self.assertEqual(V8["m5_buy_both"]["allowed_side"],"BUY")
        self.assertEqual(V8["m15_utc_06_24"]["entry_hours"],[6,24])
