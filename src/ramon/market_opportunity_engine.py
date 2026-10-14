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
                       max_spread_atr: float = 0.16) -> Opportunity:
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
    # Completed-bar momentum and price-structure alignment; no vote based on future closes.
    trend = (last.close - bars[-5].close) / atr
    shape = (mid20 - previous_mid) / atr
    direction = None
    setup = "NONE"
    if trend >= 0.28 and shape >= 0.12 and last.close >= mid20 and last.low <= mid20 + 0.35 * atr:
        direction, setup = "BUY", "TREND_PULLBACK"
    elif trend <= -0.28 and shape <= -0.12 and last.close <= mid20 and last.high >= mid20 - 0.35 * atr:
        direction, setup = "SELL", "TREND_PULLBACK"
    elif (high20 - low20) <= 3.8 * atr:
        if mid <= low20 + 0.55 * atr and last.close > last.open:
            direction, setup = "BUY", "RANGE_REJECTION"
        elif mid >= high20 - 0.55 * atr and last.close < last.open:
            direction, setup = "SELL", "RANGE_REJECTION"
    if direction is None:
        return wait("no_valid_setup")
    entry = market.ask if direction == "BUY" else market.bid
    risk = max(0.75 * atr, 2.0 * spread)
    if direction == "BUY":
        stop = min(entry-risk, low20-0.10*atr)
        target = max(entry+minimum_rr*(entry-stop), high20)
        rr = (target-entry)/(entry-stop)
    else:
        stop = max(entry+risk, high20+0.10*atr)
        target = min(entry-minimum_rr*(stop-entry), low20)
        rr = (entry-target)/(stop-entry)
    if rr < minimum_rr or not all(isfinite(v) for v in (stop,target,rr)):
        return wait("insufficient_reward_risk")
    return Opportunity(direction, setup, "candidate_only", entry, stop, target, rr, atr)
