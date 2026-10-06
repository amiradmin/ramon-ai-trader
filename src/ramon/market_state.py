"""Observable price scenarios and conservative entry routing, without future bars.

This is a deterministic policy, not a trained claim of market truth. Multiple
conditions can coexist; hazards take priority and missing evidence stays unknown.
News, account, position and execution locks remain authoritative in the EA.
"""
from statistics import median

from .core import Market, atr14

POLICY_VERSION = "market-state-v2"


def assess_market(market: Market, *, max_spread_points: int = 50) -> dict:
    bars = market.bars
    recent = bars[-12:]
    baseline_bars = bars[-65:-1]
    ranges = [max(b.high-b.low, abs(b.high-a.close), abs(b.low-a.close))
              for a, b in zip(baseline_bars, baseline_bars[1:])]
    baseline = max(median(ranges), market.point)
    atr = atr14(bars)
    prior = bars[-13:-1]
    low, high = min(b.low for b in prior), max(b.high for b in prior)
    width = high-low
    last = bars[-1]
    previous = bars[-2]
    price = market.bid
    spread = market.ask-market.bid
    movement = last.close-bars[-13].close
    path = sum(abs(b.close-a.close) for a, b in zip(bars[-13:-1], recent))
    efficiency = abs(movement)/path if path else 0.0
    direction = "BUY" if movement>0 else "SELL" if movement<0 else "NONE"
    flags = []
    def add(condition, label):
        if condition:
            flags.append(label)
    add(spread/market.point>max_spread_points or (atr>market.point and spread/atr>.35), "LOW_LIQUIDITY")
    add(atr<=market.point, "FLAT_MARKET")
    add(abs(last.open-previous.close)>=2*baseline or abs(price-last.close)>=3*baseline, "PRICE_GAP")
    add(last.high-last.low>=3*baseline or atr>=2.5*baseline, "VOLATILITY_SHOCK")
    add(efficiency<.20 and width>=6*baseline, "DISORDERLY_MARKET")
    add(atr<.55*baseline, "VOLATILITY_COMPRESSION")
    # Boundary references exclude the candle being classified.
    buffer = .10*max(atr, market.point)
    fake_up = last.high>high+max(buffer,.20*baseline) and last.close<high-buffer
    fake_down = last.low<low-max(buffer,.20*baseline) and last.close>low+buffer
    add(fake_up, "FALSE_BREAKOUT_UP")
    add(fake_down, "FALSE_BREAKOUT_DOWN")
    breakout_up = last.close>high+buffer or price>high+buffer
    breakout_down = last.close<low-buffer or price<low-buffer
    older = bars[-14:-2]
    old_high, old_low = max(b.high for b in older), min(b.low for b in older)
    retest_up = previous.close>old_high+buffer and last.low<=old_high+buffer and last.close>old_high and price>=old_high
    retest_down = previous.close<old_low-buffer and last.high>=old_low-buffer and last.close<old_low and price<=old_low
    add(retest_up, "BREAKOUT_RETEST_UP")
    add(retest_down, "BREAKOUT_RETEST_DOWN")
    add(breakout_up and not retest_up, "BREAKOUT_UP")
    add(breakout_down and not retest_down, "BREAKOUT_DOWN")
    impulse = (last.close-bars[-4].close)/max(atr, market.point)
    prior_move = bars[-4].close-bars[-13].close
    prior_path = sum(abs(b.close-a.close) for a,b in zip(bars[-13:-4], bars[-12:-3]))
    prior_efficiency = abs(prior_move)/prior_path if prior_path else 0.0
    add(prior_efficiency>=.30 and abs(prior_move)>=.75*atr
        and abs(impulse)>=.5 and impulse*prior_move<0, "REGIME_TRANSITION")
    trend = efficiency>=.30 and abs(movement)>=.75*atr
    if trend:
        pulling_back = (price-last.close)*movement<-.06*atr*abs(movement)
        add(pulling_back, "PULLBACK_UP" if direction=="BUY" else "PULLBACK_DOWN")
        add(not pulling_back, "TREND_UP" if direction=="BUY" else "TREND_DOWN")
    is_range = efficiency<.30 and 1.5*atr<=width<=6*atr and low<=price<=high
    if is_range:
        add(price<=low+.2*width, "RANGE_LOW")
        add(price>=high-.2*width, "RANGE_HIGH")
        add(low+.2*width<price<high-.2*width, "RANGE_MIDDLE")
    hazards = {"LOW_LIQUIDITY", "FLAT_MARKET", "PRICE_GAP", "VOLATILITY_SHOCK", "DISORDERLY_MARKET",
               "VOLATILITY_COMPRESSION", "REGIME_TRANSITION", "CONFLICTING_STRUCTURE"}
    add((fake_up and (fake_down or breakout_up or retest_up))
        or (fake_down and (breakout_down or retest_down)), "CONFLICTING_STRUCTURE")
    state = next((flag for flag in flags if flag in hazards), flags[0] if flags else "UNCERTAIN")
    if hazards.intersection(flags) or state=="UNCERTAIN":
        route, allowed = "WAIT", []
    elif state=="RANGE_MIDDLE":
        # A neutral position inside a measured range is no longer an absolute veto.
        # The final policy still requires the model, intrabar confirmation and the
        # independent AI-trend confirmation to agree before any order is allowed.
        route, allowed = "CONFIRMED_MODEL", ["BUY", "SELL"]
    elif state in {"RANGE_LOW", "RANGE_HIGH"}:
        route, allowed = "RANGE_REVERSAL", ["BUY" if state=="RANGE_LOW" else "SELL"]
    else:
        route = "CONFIRMED_MODEL"
        allowed = ["SELL" if state=="FALSE_BREAKOUT_UP" else "BUY" if state=="FALSE_BREAKOUT_DOWN"
                   else "BUY" if state.endswith("_UP") else "SELL"]
    return {"version": POLICY_VERSION, "state": state, "conditions": flags,
            "route": route, "allowed_directions": allowed,
            "evidence": {"atr": atr, "baseline_true_range": baseline,
                         "efficiency_12": efficiency, "range_low": low, "range_high": high,
                         "spread_atr": spread/max(atr, market.point), "impulse_3_atr": impulse},
            "scope": "price_only; news/account/execution gates remain in terminal"}


def apply_market_policy(response: dict, assessment: dict) -> None:
    """Final veto after model roles and range selection; never create an order."""
    response["market_state"] = assessment["state"]
    response["market_state_policy"] = assessment["version"]
    response["market_state_route"] = assessment["route"]
    decision = response.get("decision")
    if decision not in {"BUY", "SELL"}:
        return
    permitted = decision in assessment["allowed_directions"]
    if assessment["route"]=="RANGE_REVERSAL":
        permitted = permitted and response.get("range_execution")==1
    else:
        # Structural direction alone does not authorize a trade: retain both
        # actual model and micro confirmations, including after active roles.
        permitted = permitted and response.get("intrabar_confirmed")==1 and response.get("ai_trend_confirmed")==1
        permitted = permitted and response.get("intrabar_direction")==decision and response.get("ai_trend_direction")==decision
        permitted = permitted and response.get("range_execution", 0)==0
    if not permitted:
        response.update(decision="WAIT", reason="market_state_wait_"+assessment["state"].lower(),
                        range_execution=0)
