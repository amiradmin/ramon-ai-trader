from django.test import SimpleTestCase
from .regime_coordinator import consensus, suitability, ema

class RegimeCoordinatorTests(SimpleTestCase):
    def test_consensus_requires_agreement(self):
        self.assertEqual(consensus(["BULLISH","BULLISH"]),"BULLISH")
        self.assertEqual(consensus(["BULLISH","BEARISH"]),"UNCERTAIN")
        self.assertEqual(consensus(["BULLISH"]),"UNCERTAIN")
        self.assertEqual(consensus([]),"UNCERTAIN")
        self.assertEqual(consensus(["UNKNOWN","UNKNOWN"]),"UNCERTAIN")

    def test_suitability(self):
        self.assertEqual(suitability("TREND_PULLBACK","BULLISH"),"COMPATIBLE")
        self.assertEqual(suitability("TREND_PULLBACK","RANGING"),"MISMATCH")
        self.assertEqual(suitability("RANGE_EDGE_FADE","RANGING"),"COMPATIBLE")
        self.assertEqual(suitability("VOLATILITY_COMPRESSION_EXPANSION","BULLISH"),"SETUP_SPECIFIC_CHECK_REQUIRED")
        self.assertEqual(suitability("TREND_PULLBACK","UNCERTAIN"),"UNCERTAIN")

    def test_ema_direction(self):
        values=list(range(1,51))
        self.assertGreater(ema(values,20),ema(values,50))
        self.assertLess(ema(values[::-1],20),ema(values[::-1],50))
