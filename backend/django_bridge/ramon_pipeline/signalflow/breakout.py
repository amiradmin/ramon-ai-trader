"""Optional causal range-breakout detector."""
from dataclasses import dataclass
from statistics import median
from typing import Any,Mapping
from ._detector_utils import adx_series,atr_series,market_bars,result,safe_score,unsupported_symbol

@dataclass(frozen=True)
class BreakoutConfig:
    range_bars:int=20
    minimum_ranging_bars:int=12
    adx_ranging:float=18.0
    breakout_atr:float=.3
    atr_ratio_min:float=1.0
    max_breakout_range_atr:float=2.5
    symbol_prefixes:tuple[str,...]|None=("XAUUSD",)

class BreakoutDetector:
    def __init__(self,config:BreakoutConfig|None=None):self.config=config or BreakoutConfig()
    def evaluate(self,market:Mapping[str,Any])->dict[str,Any]:
        c=self.config
        if unsupported_symbol(market,c.symbol_prefixes):return result("REJECTED","UNSUPPORTED_SYMBOL")
        try:b=market_bars(market)
        except (KeyError,TypeError,ValueError,OverflowError):return result("REJECTED","INVALID_MARKET_DATA")
        if len(b)<max(c.range_bars+2,30,c.minimum_ranging_bars+2):return result("DEFERRED","INSUFFICIENT_BARS")
        t=len(b)-1;a=atr_series(b);d=adx_series(b)
        if not(a[t]>0 and a[t-1]>0 and d[t]==d[t] and d[t-1]==d[t-1]):return result("DEFERRED","INDICATORS_UNAVAILABLE")
        prior=b[t-c.range_bars:t];rh=max(x["high"] for x in prior);rl=min(x["low"] for x in prior)
        for off in (1,2):
            i=t-off
            if i<c.range_bars:continue
            old=b[i-c.range_bars:i];oh=max(x["high"] for x in old);ol=min(x["low"] for x in old)
            if b[i]["close"]>oh+c.breakout_atr*a[i] and b[t]["close"]<=rh:return result("REJECTED","BREAKOUT_FAILED_WITHIN_TWO_BARS")
            if b[i]["close"]<ol-c.breakout_atr*a[i] and b[t]["close"]>=rl:return result("REJECTED","BREAKOUT_FAILED_WITHIN_TWO_BARS")
        if any(x!=x or x>=c.adx_ranging for x in d[t-c.minimum_ranging_bars:t]):return result("DEFERRED","RANGE_NOT_ESTABLISHED")
        base=median(x for x in a[max(14,t-80):t] if x==x and x>0)
        oldbase=median(x for x in a[max(14,t-81):t-1] if x==x and x>0)
        ratio=a[t]/base;oldratio=a[t-1]/oldbase
        if ratio<=c.atr_ratio_min or ratio<=oldratio or d[t]<=d[t-1]:return result("DEFERRED","BREAKOUT_NOT_CONFIRMED")
        if b[t]["high"]-b[t]["low"]>c.max_breakout_range_atr*a[t]:return result("REJECTED","BREAKOUT_EXHAUSTION")
        close=b[t]["close"]
        if close>=rh+c.breakout_atr*a[t]:side,level="BUY",rh
        elif close<=rl-c.breakout_atr*a[t]:side,level="SELL",rl
        else:return result("DEFERRED","NO_RANGE_BREAKOUT")
        score=safe_score(.5+.2*min(1,ratio-1)+.3*min(1,(d[t]-d[t-1])/5))
        setup={"type":"RANGE_BREAKOUT","side":side,"range_high":rh,"range_low":rl,"breakout_level":level,
               "atr":a[t],"atr_ratio":ratio,"adx":d[t],"reference_price":close,"invalidation_price":level,
               "signal_bar_time":b[t]["time"]}
        return result("ACCEPTED","RANGE_BREAKOUT_CONFIRMED",side=side,candidate_side=side,score=score,
                      setup=setup,detail=f"{side} range breakout confirmed")
