from django.test import SimpleTestCase
from .scalp_risk_research import compare_risk


class ScalpRiskResearchTests(SimpleTestCase):
    def test_minimum_lot_and_cost_adjusted_pnl(self):
        trade = dict(entry=100, plan={'stop':95}, net_points=100)
        report = compare_risk([trade], starting_balance=1000, point='.01',
            tick_size='.01', tick_value=1, minimum='.01', step='.01',
            maximum=100, allowance_points=15)
        rows = {row['risk_percent']: row for row in report['scenarios']}
        self.assertEqual(rows['0.15']['eligible'], 0)
        self.assertEqual(rows['0.50']['eligible'], 0)
        self.assertEqual(rows['0.75']['eligible'], 1)
        self.assertEqual(rows['0.75']['net_profit'], 1)
        self.assertIsNone(rows['0.15']['win_rate_percent'])
