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

    def test_exact_closure_does_not_excuse_larger_gap(self):
        from datetime import datetime, timedelta, timezone
        from .regime_coordinator import gap_details
        a=datetime(2026,11,23,21,tzinfo=timezone.utc)
        closure=[(a+timedelta(hours=1),a+timedelta(hours=2))]
        self.assertEqual(gap_details([a,a+timedelta(hours=2)],60,closure)[0]["classification"],"VERIFIED_CLOSURE")
        self.assertEqual(gap_details([a,a+timedelta(hours=3)],60,closure)[0]["classification"],"UNVERIFIED_GAP")
        self.assertEqual(gap_details([a,a+timedelta(hours=2)],60)[0]["classification"],"UNVERIFIED_GAP")

    def test_alignment_unconfirmed_h1(self):
        from .regime_coordinator import alignment
        context={"M5":{"status":"READY","direction":"BUY"},
                 "M15":{"status":"READY","direction":"SELL"},
                 "H1":{"status":"GAP_OR_OFF_GRID","direction":None}}
        self.assertEqual(alignment(context),"SHORT_TERM_CONFLICT_H1_UNCONFIRMED")
