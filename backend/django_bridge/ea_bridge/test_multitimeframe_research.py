from django.test import SimpleTestCase
from .replay_backtest import _completed_trend_closes, _confirmation_reason
from .management.commands.evaluate_trend_pullback import V7


class MultitimeframeResearchTests(SimpleTestCase):
    def test_complete_groups_only(self):
        rows = [{"time": i*300, "close": 100+i} for i in range(8)]
        self.assertEqual(_completed_trend_closes(rows, 900, 300),
                         [(900, 102), (1800, 105)])
        self.assertEqual(_completed_trend_closes(rows[:1]+rows[2:], 900, 300),
                         [(1800, 105)])

    def test_h1_requires_twelve_complete_m5_bars(self):
        rows = [{"time": i*300, "close": 100+i} for i in range(12)]
        self.assertEqual(_completed_trend_closes(rows, 3600, 300), [(3600,111)])
        self.assertEqual(_completed_trend_closes(rows[:-1], 3600, 300), [])

    def test_future_context_cannot_satisfy_warmup(self):
        closes = [(900*(i+1), 100+i) for i in range(60)]
        self.assertEqual(_confirmation_reason(closes, 49*900, 900, "BUY"), "WARMUP")

    def test_future_context_cannot_reverse_confirmation(self):
        closes = [(900*(i+1), 100+i) for i in range(50)]
        closes += [(900*(i+51), 1) for i in range(100)]
        self.assertIsNone(_confirmation_reason(closes, 50*900, 900, "BUY"))
        self.assertEqual(_confirmation_reason(closes, 50*900, 900, "SELL"),
                         "DIRECTION_MISMATCH")

    def test_stale_context(self):
        closes = [(900*(i+1), 100+i) for i in range(50)]
        self.assertEqual(_confirmation_reason(closes, 51*900, 900, "BUY"), "STALE")

    def test_variants(self):
        self.assertEqual(len(V7), 7)
        self.assertEqual(V7["m5_sell_only"]["allowed_side"], "SELL")
        self.assertEqual(V7["m5_buy_only"]["allowed_side"], "BUY")
        self.assertEqual(V7["m5_m15_h1"]["confirm_timeframes"], ["M15", "H1"])
