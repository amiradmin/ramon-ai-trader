"""Optional range-bound mean-reversion detector."""
from dataclasses import dataclass
from typing import Any,Mapping
from ._detector_utils import adx_series,atr_series,market_bars,result,rsi_series,safe_score,unsupported_symbol

@dataclass(frozen=True)
class RangeReversionConfig:
    range_bars:int=30
    adx_ranging:float=18.0
    edge_tolerance_atr:float=.35
    rsi_oversold:float=30.0
    rsi_overbought:float=70.0
    symbol_prefixes:tuple[str,...]|None=("XAUUSD",)

class RangeReversionDetector:
    def __init__(self,config:RangeReversionConfig|None=None):self.config=config or RangeReversionConfig()
    def evaluate(self,market:Mapping[str,Any])->dict[str,Any]:
        c=self.config
        if unsupported_symbol(market,c.symbol_prefixes):return result("REJECTED","UNSUPPORTED_SYMBOL")
        try:b=market_bars(market)
        except (KeyError,TypeError,ValueError,OverflowError):return result("REJECTED","INVALID_MARKET_DATA")
        if len(b)<max(c.range_bars+1,30):return result("DEFERRED","INSUFFICIENT_BARS")
        t=len(b)-1;a,d,r=atr_series(b),adx_series(b),rsi_series(b)
        if not(a[t]>0 and d[t]==d[t] and r[t]==r[t]):return result("DEFERRED","INDICATORS_UNAVAILABLE")
        if d[t]>=c.adx_ranging:return result("REJECTED","MARKET_NOT_RANGING")
        prior=b[t-c.range_bars:t];hi=max(x["high"] for x in prior);lo=min(x["low"] for x in prior);mid=(hi+lo)/2
        bar=b[t];body=abs(bar["close"]-bar["open"]);lw=min(bar["open"],bar["close"])-bar["low"];uw=bar["high"]-max(bar["open"],bar["close"])
        side=None
        if bar["low"]<=lo+c.edge_tolerance_atr*a[t] and lw>body and bar["close"]>bar["open"] and r[t]<c.rsi_oversold:side="BUY"
        elif bar["high"]>=hi-c.edge_tolerance_atr*a[t] and uw>body and bar["close"]<bar["open"] and r[t]>c.rsi_overbought:side="SELL"
        if side is None:return result("DEFERRED","NO_RANGE_REJECTION")
        ref=bar["close"]
        if (side=="BUY" and mid<=ref) or (side=="SELL" and mid>=ref):return result("REJECTED","RANGE_MIDPOINT_NOT_AHEAD")
        if abs(mid-ref)>hi-lo:return result("REJECTED","RANGE_TARGET_TOO_FAR")
        score=safe_score(.55+min(.25,abs(50-r[t])/100)+min(.2,(c.adx_ranging-d[t])/100))
        setup={"type":"RANGE_MEAN_REVERSION","side":side,"range_high":hi,"range_low":lo,"range_start_time":prior[0]["time"],"range_midpoint":mid,
               "target_price":mid,"reference_price":ref,"invalidation_price":lo if side=="BUY" else hi,
               "atr":a[t],"adx":d[t],"rsi":r[t],"signal_bar_time":bar["time"]}
        return result("ACCEPTED","RANGE_MEAN_REVERSION_CONFIRMED",side=side,candidate_side=side,
                      score=score,setup=setup,detail=f"{side} range-edge rejection; midpoint target {mid:.2f}",market=market)
