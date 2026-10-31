"""Causal helpers for optional SignalFlow detectors."""
import math
from typing import Any, Mapping, Sequence

def market_bars(market: Mapping[str, Any]) -> list[dict[str, float]]:
    raw=market.get("bars")
    if not isinstance(raw,Sequence) or isinstance(raw,(str,bytes)): raise ValueError("bars must be a sequence")
    out=[]; last=None
    for x in raw:
        t=float(x["time"]); o,h,l,c=(float(x[k]) for k in ("open","high","low","close"))
        if not all(math.isfinite(v) for v in (t,o,h,l,c)) or l<=0 or h<l or not l<=c<=h: raise ValueError("invalid OHLC")
        if last is not None and t<=last: raise ValueError("bars not chronological")
        last=t; out.append({"time":t,"open":o,"high":h,"low":l,"close":c})
    return out

def true_ranges(bars):
    return [max(b["high"]-b["low"],abs(b["high"]-(bars[i-1]["close"] if i else b["close"])),abs(b["low"]-(bars[i-1]["close"] if i else b["close"]))) for i,b in enumerate(bars)]

def atr_series(bars,period=14):
    tr=true_ranges(bars); out=[float("nan")]*len(tr)
    if len(tr)<period:return out
    v=sum(tr[:period])/period;out[period-1]=v
    for i in range(period,len(tr)):v=(v*(period-1)+tr[i])/period;out[i]=v
    return out

def rsi_series(bars,period=14):
    c=[b["close"] for b in bars];out=[float("nan")]*len(c)
    if len(c)<=period:return out
    g=[max(c[i]-c[i-1],0) for i in range(1,len(c))];l=[max(c[i-1]-c[i],0) for i in range(1,len(c))]
    ag,al=sum(g[:period])/period,sum(l[:period])/period
    out[period]=100 if al==0 and ag else 50 if al==0 else 100-100/(1+ag/al)
    for i in range(period+1,len(c)):
        j=i-1;ag=(ag*(period-1)+g[j])/period;al=(al*(period-1)+l[j])/period
        out[i]=100 if al==0 and ag else 50 if al==0 else 100-100/(1+ag/al)
    return out

def adx_series(bars,period=14):
    n=len(bars);out=[float("nan")]*n
    if n<2*period+1:return out
    tr=true_ranges(bars);p=[0.0]*n;m=[0.0]*n
    for i in range(1,n):
        up=bars[i]["high"]-bars[i-1]["high"];dn=bars[i-1]["low"]-bars[i]["low"]
        p[i]=up if up>dn and up>0 else 0;m[i]=dn if dn>up and dn>0 else 0
    ts,ps,ms=sum(tr[1:period+1]),sum(p[1:period+1]),sum(m[1:period+1]);dx=[float("nan")]*n
    for i in range(period,n):
        if i>period:ts+=tr[i]-ts/period;ps+=p[i]-ps/period;ms+=m[i]-ms/period
        a,b=(100*ps/ts,100*ms/ts) if ts else (0,0);dx[i]=100*abs(a-b)/(a+b) if a+b else 0
    first=2*period-1;v=sum(dx[period:first+1])/period;out[first]=v
    for i in range(first+1,n):v=(v*(period-1)+dx[i])/period;out[i]=v
    return out

def result(status,reason,*,side="HOLD",candidate_side="NONE",score=0.0,setup=None,detail=""):
    ok=status=="ACCEPTED"
    return {"approved":ok,"status":status,"side":side if ok else "HOLD","candidate_side":candidate_side,
            "score":round(float(score),4),"reason":reason,"explanation":detail or reason,"regime":None,
            "components":{},"checks":[],"signal_bar_time":None,"setup":setup or {}}

def unsupported_symbol(market,prefixes):
    s=str(market.get("symbol","")).strip().upper()
    return bool(s and prefixes and not s.startswith(tuple(p.upper() for p in prefixes)))

def safe_score(x):return max(0.0,min(1.0,x))
