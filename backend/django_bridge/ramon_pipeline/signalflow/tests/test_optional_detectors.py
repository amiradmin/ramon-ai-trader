from unittest import TestCase
from unittest.mock import patch
from ramon_pipeline.signalflow._detector_utils import atr_series,adx_series,rsi_series
from ramon_pipeline.signalflow.breakout import BreakoutDetector
from ramon_pipeline.signalflow.opportunities import OpportunityConfig,OpportunityDetector
from ramon_pipeline.signalflow.tests.test_opportunities import LONG_SETUP,SHORT_SETUP,UPTREND,make_bars,snapshot
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
        self.assertEqual(cfg.symbol_overrides,{})
        legacy=OpportunityDetector().evaluate(snapshot(make_bars(LONG_SETUP)))
        self.assertNotIn("pullback_variant",legacy["setup"])
    def test_opt_in_pullback_options_preserve_long_short_symmetry(self):
        cases=((LONG_SETUP,2400.0,"BUY"),(SHORT_SETUP,2600.0,"SELL"))
        configs=(
            OpportunityConfig(consider_previous_impulse=True),
            OpportunityConfig(shallow_pullback_mode=True),
            OpportunityConfig(zone_reference="SLOW_EMA",zone_tolerance_atr=5.0),
        )
        for config in configs:
            sides=[]
            for legs,start,expected in cases:
                result=OpportunityDetector(config=config).evaluate(snapshot(make_bars(legs,start_price=start)))
                self.assertTrue(result["approved"],(config,result["reason"]))
                self.assertEqual(result["side"],expected)
                sides.append(result["side"])
            self.assertEqual(sides,["BUY","SELL"])

    def test_shallow_pullback_requires_fast_ema_touch_then_resumption(self):
        legs=UPTREND+[(12,11),(4,-2.0),(2,1.5)]
        result=OpportunityDetector(config=OpportunityConfig(shallow_pullback_mode=True)).evaluate(
            snapshot(make_bars(legs)))
        self.assertTrue(result["approved"],result["reason"])
        self.assertEqual(result["setup"]["pullback_variant"],"SHALLOW")

    def test_symbol_specific_threshold_override_is_applied(self):
        cfg=OpportunityConfig(symbol_overrides={"XAUUSD_o":{"min_impulse_atr":100.0}})
        result=OpportunityDetector(config=cfg).evaluate(snapshot(make_bars(LONG_SETUP)))
        self.assertEqual(result["reason"],"IMPULSE_TOO_SMALL")

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


    def test_indicator_values_are_prefix_causal(self):
        bars=[]
        price=2000.0
        for i in range(80):
            close=price+(0.15 if i%3 else -0.1)
            bars.append({"time":1700000000+i*900,"open":price,"high":max(price,close)+.5,
                         "low":min(price,close)-.5,"close":close})
            price=close
        for fn in (atr_series,adx_series,rsi_series):
            prefix=fn(bars[:-1])[-1]
            extended=fn(bars)[-2]
            self.assertAlmostEqual(prefix,extended,places=10)

    def test_breakout_detector_is_mirror_symmetric(self):
        bars=[]
        for i in range(60):
            bars.append({"time":1700000000+i*900,"open":100.0,"high":101.0,"low":99.0,"close":100.0})
        bars.append({"time":1700000000+60*900,"open":101.0,"high":101.7,"low":100.8,"close":101.5})
        mirrored=[{"time":b["time"],"open":200-b["open"],"high":200-b["low"],
                   "low":200-b["high"],"close":200-b["close"]} for b in bars]
        def atr_mock(series): return [1.0]*(len(series)-1)+[1.2]
        def adx_mock(series): return [10.0]*(len(series)-1)+[11.0]
        with patch("ramon_pipeline.signalflow.breakout.atr_series",side_effect=atr_mock), patch("ramon_pipeline.signalflow.breakout.adx_series",side_effect=adx_mock):
            long=BreakoutDetector().evaluate({"symbol":"XAUUSD_o","bars":bars})
            short=BreakoutDetector().evaluate({"symbol":"XAUUSD_o","bars":mirrored})
        self.assertTrue(long["approved"],long["reason"])
        self.assertTrue(short["approved"],short["reason"])
        self.assertEqual((long["side"],short["side"]),("BUY","SELL"))
        self.assertTrue(long["signal_id"] and short["signal_id"])
        self.assertEqual(long["signal_bar_time"],bars[-1]["time"])

    def test_range_reversion_detector_is_mirror_symmetric(self):
        bars=[]
        for i in range(60):
            bars.append({"time":1700000000+i*900,"open":100.0,"high":101.0,"low":99.0,"close":100.0})
        bars.append({"time":1700000000+60*900,"open":99.4,"high":99.8,"low":98.7,"close":99.6})
        mirrored=[{"time":b["time"],"open":200-b["open"],"high":200-b["low"],
                   "low":200-b["high"],"close":200-b["close"]} for b in bars]
        def atr_mock(series): return [1.0]*len(series)
        def adx_mock(series): return [10.0]*len(series)
        with patch("ramon_pipeline.signalflow.range_reversion.atr_series",side_effect=atr_mock), patch("ramon_pipeline.signalflow.range_reversion.adx_series",side_effect=adx_mock), patch("ramon_pipeline.signalflow.range_reversion.rsi_series",return_value=[20.0]*len(bars)):
            long=RangeReversionDetector().evaluate({"symbol":"XAUUSD_o","bars":bars})
        with patch("ramon_pipeline.signalflow.range_reversion.atr_series",side_effect=atr_mock), patch("ramon_pipeline.signalflow.range_reversion.adx_series",side_effect=adx_mock), patch("ramon_pipeline.signalflow.range_reversion.rsi_series",return_value=[80.0]*len(bars)):
            short=RangeReversionDetector().evaluate({"symbol":"XAUUSD_o","bars":mirrored})
        self.assertTrue(long["approved"],long["reason"])
        self.assertTrue(short["approved"],short["reason"])
        self.assertEqual((long["side"],short["side"]),("BUY","SELL"))

    def test_multi_setup_reservation_can_be_released_after_downstream_defer(self):
        class Stub:
            def evaluate(self,market):
                return {"approved":True,"status":"ACCEPTED","side":"BUY","candidate_side":"BUY",
                        "score":.8,"reason":"OK","signal_bar_time":123,
                        "setup":{"type":"TREND_PULLBACK","anchor_time":100}}
        detector=MultiSetupDetector(trend_detector=Stub())
        market={"symbol":"XAUUSD_o","timeframe":"M15","bars":[{"time":123}]}
        first=detector.evaluate(market)
        self.assertTrue(first["approved"])
        self.assertTrue(detector.release(first["dedupe_key"],first["signal_bar_time"]))
        self.assertTrue(detector.evaluate(market)["approved"])

    def test_multi_setup_release_reaches_wrapped_trend_detector(self):
        bars=make_bars(LONG_SETUP+[(1,1.0),(1,.8)])
        detector=MultiSetupDetector(trend_detector=OpportunityDetector())
        first=detector.evaluate(snapshot(bars[:-2]))
        self.assertTrue(first["approved"],first["reason"])
        self.assertTrue(detector.release(first["dedupe_key"],first["signal_bar_time"]))
        later=detector.evaluate(snapshot(bars[:-1]))
        self.assertTrue(later["approved"],later["reason"])

    def test_highest_scoring_same_side_candidate_wins(self):
        class Stub:
            def __init__(self,kind,score):
                self.kind,self.score=kind,score
            def evaluate(self,market):
                return {"approved":True,"status":"ACCEPTED","side":"BUY","candidate_side":"BUY",
                        "score":self.score,"reason":"OK","setup":{"type":self.kind}}
        detector=MultiSetupDetector(config=MultiSetupConfig(enable_breakout=True),
                                    trend_detector=Stub("TREND_PULLBACK",.7),
                                    breakout_detector=Stub("RANGE_BREAKOUT",.9))
        result=detector.evaluate({})
        self.assertEqual(result["setup"]["type"],"RANGE_BREAKOUT")
        self.assertEqual(len(result["candidates"]),2)

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
