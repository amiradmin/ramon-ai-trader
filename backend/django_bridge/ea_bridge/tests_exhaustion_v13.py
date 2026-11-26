from django.test import SimpleTestCase
from ramon_pipeline.signalflow.additional_setups import exhaustion_v2
from ramon_pipeline.signalflow.opportunities import OpportunityDetector


class ExhaustionV13Tests(SimpleTestCase):
    def evaluate(self, *, gap=False, pivot=2, early_break=False, buy=False):
        closes=[100,101,100,103,105,106,99]
        if early_break:
            closes[5]=99
        opens=[100,100,101,100,103,105,105]
        highs=[max(o,c)+1 for o,c in zip(opens,closes)]
        lows=[min(o,c)-1 for o,c in zip(opens,closes)]
        # A confirmed pivot low at index 2, known at index 4.
        lows[2]=100
        times=[900*i for i in range(len(closes))]
        if gap:
            times[-1]+=900
        rsi=[50,50,50,50,80,50,45]
        if buy:
            opens=[200-v for v in opens]
            closes=[200-v for v in closes]
            highs,lows=[200-v for v in lows],[200-v for v in highs]
            rsi=[100-v for v in rsi]
        cfg=dict(extension_lookback_bars=3,minimum_extension_atr=2,
                 rsi_extreme=70,research_pivot_window=2)
        return exhaustion_v2(times,opens,highs,lows,closes,[1]*7,rsi,
                             [pivot] if buy else [],[] if buy else [pivot],cfg)

    def test_delayed_sell_confirmation(self):
        result=self.evaluate()
        self.assertEqual(result["side"],"SELL")
        self.assertEqual(result["exhaustion_age_bars"],2)
        self.assertEqual(result["invalidation_price"],107)

    def test_symmetric_buy_confirmation(self):
        result=self.evaluate(buy=True)
        self.assertEqual(result["side"],"BUY")
        self.assertEqual(result["invalidation_price"],93)

    def test_missing_candle_blocks_candidate(self):
        self.assertIsNone(self.evaluate(gap=True))

    def test_pivot_not_known_at_event(self):
        self.assertIsNone(self.evaluate(pivot=3))

    def test_prior_break_disqualifies_new_signal(self):
        self.assertIsNone(self.evaluate(early_break=True))

    def test_default_detector_has_no_research_policy(self):
        detector=OpportunityDetector()
        self.assertIsNone(detector.research_transition_setup)
        self.assertFalse(detector.research_exhaustion_v2)
