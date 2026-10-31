"""Opt-in orchestration for independent setup detectors."""
from dataclasses import dataclass
from typing import Any,Mapping
from .breakout import BreakoutDetector
from .opportunities import OpportunityDetector
from .range_reversion import RangeReversionDetector
from .reversal import ReversalDetector

@dataclass(frozen=True)
class MultiSetupConfig:
    enable_breakout:bool=False
    enable_range_reversion:bool=False
    enable_reversal:bool=False

class MultiSetupDetector:
    def __init__(self,*,config:MultiSetupConfig|None=None,trend_detector=None,breakout_detector=None,
                 range_reversion_detector=None,reversal_detector=None):
        self.config=config or MultiSetupConfig()
        self.trend_detector=trend_detector or OpportunityDetector()
        self.breakout_detector=breakout_detector or BreakoutDetector()
        self.range_reversion_detector=range_reversion_detector or RangeReversionDetector()
        self.reversal_detector=reversal_detector or ReversalDetector()
        self._seen=set()
    def _dedupe(self,market,candidate):
        if not candidate.get("approved"):return candidate
        setup=candidate.get("setup") or {}
        kind=setup.get("type","UNKNOWN")
        bars=market.get("bars") or []
        current_time=(bars[-1].get("time") if bars else candidate.get("signal_bar_time"))
        anchor=setup.get("anchor_time",setup.get("range_start_time",setup.get("signal_bar_time",current_time)))
        key=(str(market.get("symbol","")),str(market.get("timeframe","M15")),kind,candidate.get("side"),anchor)
        if key in self._seen:
            return {**candidate,"approved":False,"status":"REJECTED","side":"HOLD",
                    "reason":"DUPLICATE_SIGNAL","explanation":f"duplicate {kind} candidate"}
        self._seen.add(key)
        return candidate
    def evaluate(self,market:Mapping[str,Any])->dict[str,Any]:
        candidates=[self._dedupe(market,self.trend_detector.evaluate(market))]
        if self.config.enable_breakout:candidates.append(self._dedupe(market,self.breakout_detector.evaluate(market)))
        if self.config.enable_range_reversion:candidates.append(self._dedupe(market,self.range_reversion_detector.evaluate(market)))
        if self.config.enable_reversal:candidates.append(self._dedupe(market,self.reversal_detector.evaluate(market)))
        accepted=[x for x in candidates if x.get("approved") and x.get("status")=="ACCEPTED"]
        if len({x.get("side") for x in accepted})>1:
            out=dict(accepted[0]);out.update(approved=False,status="DEFERRED",side="HOLD",candidate_side="NONE",
                reason="SETUP_CONFLICT",explanation="opposing accepted setup candidates conflict on this bar",
                candidates=candidates)
            return out
        if accepted:return {**max(accepted,key=lambda x:float(x.get("score",0))),"candidates":candidates}
        winner=next((x for x in candidates if x.get("status")=="DEFERRED"),candidates[0])
        return {**winner,"candidates":candidates}
