"""Opt-in orchestration for independent setup detectors."""
from dataclasses import dataclass
import hashlib
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
        self._seen={}
        self._release_targets={}
    def _candidate(self,market,detector,default_type):
        candidate=dict(detector.evaluate(market))
        setup=dict(candidate.get("setup") or {})
        setup.setdefault("type",default_type)
        setup.setdefault("pullback_variant",None)
        setup.setdefault("impulse_rank",None)
        candidate["setup"]=setup
        return self._dedupe(market,candidate,detector)
    def _dedupe(self,market,candidate,detector=None):
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
        dedupe_key=hashlib.sha256(repr(key).encode()).hexdigest()[:24]
        signal_time=candidate.get("signal_bar_time",setup.get("signal_bar_time"))
        self._seen[key]=signal_time
        self._release_targets[dedupe_key]=(key,detector,candidate.get("dedupe_key"))
        return {**candidate,"dedupe_key":dedupe_key}
    def release(self,dedupe_key,bar_time=None):
        target=self._release_targets.get(dedupe_key)
        if target is None:return False
        key,detector,source_key=target
        stored_time=self._seen.get(key)
        if bar_time is not None and stored_time is not None and float(stored_time)!=float(bar_time):return False
        self._seen.pop(key,None)
        self._release_targets.pop(dedupe_key,None)
        if source_key and callable(getattr(detector,"release",None)):
            detector.release(source_key,bar_time)
        return True
    def evaluate(self,market:Mapping[str,Any])->dict[str,Any]:
        candidates=[self._candidate(market,self.trend_detector,"TREND_PULLBACK")]
        if self.config.enable_breakout:candidates.append(self._candidate(market,self.breakout_detector,"RANGE_BREAKOUT"))
        if self.config.enable_range_reversion:candidates.append(self._candidate(market,self.range_reversion_detector,"RANGE_MEAN_REVERSION"))
        if self.config.enable_reversal:candidates.append(self._candidate(market,self.reversal_detector,"REVERSAL"))
        accepted=[x for x in candidates if x.get("approved") and x.get("status")=="ACCEPTED"]
        if len({x.get("side") for x in accepted})>1:
            for candidate in accepted:
                self.release(candidate.get("dedupe_key"),candidate.get("signal_bar_time"))
            out=dict(accepted[0]);out.update(approved=False,status="DEFERRED",side="HOLD",candidate_side="NONE",
                reason="SETUP_CONFLICT",explanation="opposing accepted setup candidates conflict on this bar",
                candidates=candidates)
            return out
        if accepted:
            winner=max(accepted,key=lambda x:float(x.get("score",0)))
            for candidate in accepted:
                if candidate is not winner:
                    self.release(candidate.get("dedupe_key"),candidate.get("signal_bar_time"))
            return {**winner,"candidates":candidates}
        winner=next((x for x in candidates if x.get("status")=="DEFERRED"),candidates[0])
        return {**winner,"candidates":candidates}
