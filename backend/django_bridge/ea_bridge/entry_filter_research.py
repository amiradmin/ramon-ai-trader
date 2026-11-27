"""Fixed offline hypotheses, applied only after normal pipeline approval."""
from .entry_quality_research import features

PLAN={
    "TREND_PULLBACK":(.5,("baseline","score85","utc06_12","score85_utc06_12")),
    "VOLATILITY_COMPRESSION_EXPANSION":(1.,("baseline","stop2_25","score75","ranging")),
    "SESSION_OPENING_RANGE_BREAKOUT":(.5,("baseline","buy_only","utc06_18","buy_utc06_18")),
}

def validate_filter(data, timeframe, exit_name):
    name=data.get("v15_entry_filter")
    if name is None:return None
    setups=data.get("setups",[])
    if (len(setups)!=1 or setups[0] not in PLAN or name not in PLAN[setups[0]][1]
        or timeframe!="M15" or exit_name!="baseline" or not data.get("all_strategy_risk_research")
        or data.get("trend_research") or data.get("v15_entry_audit")):
        raise ValueError("Invalid isolated V15.2.4 entry filter configuration")
    return name

def filter_reason(name, result, market):
    if name in (None,"baseline"):return None
    f=features(result,market)
    if name in ("score85","score85_utc06_12","score75"):
        threshold=.75 if name=="score75" else .85
        if f["score"] is None:return "V15_FILTER_SCORE_MISSING"
        if f["score"]<threshold:return "V15_FILTER_SCORE"
    if name in ("utc06_12","score85_utc06_12","utc06_18","buy_utc06_18"):
        end=12 if name in ("utc06_12","score85_utc06_12") else 18
        if not 6<=f["hour"]<end:return "V15_FILTER_UTC"
    if name=="stop2_25":
        if f["stop_atr"] is None:return "V15_FILTER_STOP_ATR_MISSING"
        if not 2<=f["stop_atr"]<2.5:return "V15_FILTER_STOP_ATR"
    if name=="ranging" and f["regime"]!="RANGING":return "V15_FILTER_REGIME"
    if name in ("buy_only","buy_utc06_18") and result["action"]!="BUY":return "V15_FILTER_SIDE"
    return None
