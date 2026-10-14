"""Deterministic market-structure opportunity engine for XAUUSD.

Standalone strategy research component: emits candidate geometry, never orders.
Uses only completed bars and current quoted bid/ask; no future price fields.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from math import isfinite
from .core import Market, atr14


@dataclass(frozen=True)
class Opportunity:
    decision: str
    setup: str
    reason: str
    entry: float
    stop: float
    target: float
    reward_risk: float
    atr: float

    def to_dict(self) -> dict:
        return asdict(self)


def detect_opportunity(market: Market, *, minimum_rr: float = 1.5,
                       max_spread_atr: float = 0.16,
                       require_reversal_confirmation: bool = False) -> Opportunity:
    market.validate()
    bars = market.bars
    atr = atr14(bars)
    mid = (market.bid + market.ask) / 2
    spread = market.ask - market.bid
    def wait(reason: str) -> Opportunity:
        return Opportunity("WAIT", "NONE", reason, mid, mid, mid, 0.0, atr)
    if atr <= 0 or not isfinite(atr):
        return wait("invalid_atr")
    if spread > atr * max_spread_atr:
        return wait("spread_too_wide")
    if len(bars) < 35:
        return wait("insufficient_bars")
    recent = bars[-21:-1]
    earlier = bars[-35:-21]
    high20, low20 = max(b.high for b in recent), min(b.low for b in recent)
    previous_mid = (max(b.high for b in earlier) + min(b.low for b in earlier)) / 2
    mid20 = (high20 + low20) / 2
    last = bars[-1]
    # Trend is established BEFORE the pullback, avoiding the contradictory
    # requirement that the final pullback candle must itself show momentum.
    last = bars[-1]
    impulse = bars[-13:-4]
    impulse_first, impulse_last = impulse[0].close, impulse[-1].close
    impulse_move = (impulse_last - impulse_first) / atr
    pullback = bars[-4:-1]
    direction = None
    setup = "NONE"
    structural_stop = None
    structural_target = None
    if impulse_move >= 0.9:
        prior_high = max(x.high for x in impulse)
        pull_low = min(x.low for x in pullback)
        retracement = (prior_high-pull_low)/atr
        trigger = last.close > last.open and last.close > bars[-2].close
        if 0.20 <= retracement <= 1.8 and trigger and last.close < prior_high:
            direction, setup = "BUY", "TREND_PULLBACK"
            structural_stop = pull_low - 0.12*atr
            structural_target = prior_high
    elif impulse_move <= -0.9:
        prior_low = min(x.low for x in impulse)
        pull_high = max(x.high for x in pullback)
        retracement = (pull_high-prior_low)/atr
        trigger = last.close < last.open and last.close < bars[-2].close
        if 0.20 <= retracement <= 1.8 and trigger and last.close > prior_low:
            direction, setup = "SELL", "TREND_PULLBACK"
            structural_stop = pull_high + 0.12*atr
            structural_target = prior_low

    if direction is None and (high20 - low20) <= 3.8 * atr:
        if mid <= low20 + 0.55 * atr and last.close > last.open:
            direction, setup = "BUY", "RANGE_REJECTION"
        elif mid >= high20 - 0.55 * atr and last.close < last.open:
            direction, setup = "SELL", "RANGE_REJECTION"
    if direction is None:
        return wait("no_valid_setup")
    entry = market.ask if direction == "BUY" else market.bid
    risk = max(0.75 * atr, 2.0 * spread)
    if direction == "BUY":
        stop = min(entry-risk, structural_stop if structural_stop is not None else low20-0.10*atr)
        target = structural_target if structural_target is not None else high20
        rr = (target-entry)/(entry-stop)
    else:
        stop = max(entry+risk, structural_stop if structural_stop is not None else high20+0.10*atr)
        target = structural_target if structural_target is not None else low20
        rr = (entry-target)/(stop-entry)
    # Optional entry timing: only confirm after a completed directional
    # rejection candle, not merely because a range boundary was touched.
    # Default remains legacy behavior for comparable baseline backtests.
    if require_reversal_confirmation:
        if setup == "RANGE_REJECTION":
            prev = bars[-2]
            body = abs(last.close-last.open)
            candle_range = max(last.high-last.low, 1e-9)
            if direction == "BUY":
                confirmed = (last.close > last.open and
                             last.close > prev.close and
                             (last.close-last.low)/candle_range >= 0.65 and
                             body >= 0.12*atr)
            else:
                confirmed = (last.close < last.open and
                             last.close < prev.close and
                             (last.high-last.close)/candle_range >= 0.65 and
                             body >= 0.12*atr)
            if not confirmed:
                return wait("reversal_confirmation_missing")
        elif setup == "TREND_PULLBACK":
            prev = bars[-2]
            confirmed = (last.close > prev.high if direction == "BUY"
                         else last.close < prev.low)
            if not confirmed:
                return wait("reversal_confirmation_missing")
    # Reject setups when the historical structure cannot fund the required reward/risk.
    if rr < minimum_rr or not all(isfinite(v) for v in (stop,target,rr)):
        return wait("insufficient_reward_risk")
    return Opportunity(direction, setup, "candidate_only", entry, stop, target, rr, atr)
