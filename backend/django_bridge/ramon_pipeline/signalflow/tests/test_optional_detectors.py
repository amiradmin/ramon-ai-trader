from unittest import TestCase
from ramon_pipeline.signalflow.breakout import BreakoutDetector
from ramon_pipeline.signalflow.opportunities import OpportunityConfig
from ramon_pipeline.signalflow.multi_setup import MultiSetupConfig,MultiSetupDetector
from ramon_pipeline.signalflow.range_reversion import RangeReversionDetector
from ramon_pipeline.signalflow.reversal import ReversalDetector

class OptionalDetectorContractTests(TestCase):
    def test_reversal_stub_never_approves(self):
        r=ReversalDetector().evaluate({})
        self.assertEqual((r["status"],r["reason"]),("DEFERRED","NOT_IMPLEMENTED"))
        self.assertFalse(r["approved"])
    def test_new_families_are_disabled_by_default(self):
        self.assertEqual(MultiSetupConfig(),MultiSetupConfig(False,False,False))
        cfg=OpportunityConfig()
        self.assertFalse(cfg.consider_previous_impulse)
        self.assertFalse(cfg.shallow_pullback_mode)
        self.assertEqual(cfg.zone_reference,"FAST_EMA")
    def test_symbol_prefix_gate(self):
        self.assertEqual(BreakoutDetector().evaluate({"symbol":"EURUSD","bars":[]})["reason"],"UNSUPPORTED_SYMBOL")
    def test_identical_prefix_is_deterministic(self):
        bars=[];price=2000.0
        for i in range(100):
            close=price+(.1 if i%2 else -.1)
            bars.append({"time":1700000000+i*900,"open":price,"high":max(price,close)+.4,
                         "low":min(price,close)-.4,"close":close});price=close
        market={"symbol":"XAUUSD_o","bars":bars}
        for detector in (BreakoutDetector(),RangeReversionDetector()):
            self.assertEqual(detector.evaluate(market),detector.evaluate(market))
    def test_dedupe_is_scoped_by_setup_type_and_anchor(self):
        class Stub:
            def evaluate(self,market):
                return {"approved":True,"status":"ACCEPTED","side":"BUY","candidate_side":"BUY",
                        "score":.8,"reason":"OK","signal_bar_time":123,
                        "setup":{"type":"RANGE_BREAKOUT","signal_bar_time":123}}
        detector=MultiSetupDetector(trend_detector=Stub())
        market={"symbol":"XAUUSD_o","timeframe":"M15","bars":[{"time":123}]}
        self.assertTrue(detector.evaluate(market)["approved"])
        duplicate=detector.evaluate(market)
        self.assertEqual(duplicate["reason"],"DUPLICATE_SIGNAL")

    def test_opposite_accepted_candidates_conflict(self):
        class Stub:
            def __init__(self,side):self.side=side
            def evaluate(self,market):
                return {"approved":True,"status":"ACCEPTED","side":self.side,"candidate_side":self.side,
                        "score":.9,"reason":"OK","setup":{"type":"STUB"}}
        detector=MultiSetupDetector(config=MultiSetupConfig(enable_breakout=True),
                                    trend_detector=Stub("BUY"),breakout_detector=Stub("SELL"))
        r=detector.evaluate({})
        self.assertEqual((r["status"],r["reason"]),("DEFERRED","SETUP_CONFLICT"))
        self.assertEqual(len(r["candidates"]),2)
