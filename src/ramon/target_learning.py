from __future__ import annotations

from dataclasses import asdict, dataclass

from .core import Market


@dataclass(frozen=True, slots=True)
class TargetStructure:
    """Learning-only multi-target map derived from the last counter-move impulse.

    These targets are telemetry in v0.31. They MUST NOT change live execution.
    """

    ready: int
    method: str
    direction: str
    impulse_start: float
    impulse_end: float
    impulse_range: float
    impulse_atr: float
    tp1: float
    tp2: float
    tp3: float
    legacy_target: float

    def to_dict(self) -> dict[str, str | int | float]:
        return asdict(self)


def _fallback(market: Market, direction: str, target_distance: float) -> TargetStructure:
    entry = market.ask if direction == "BUY" else market.bid
    sign = 1.0 if direction == "BUY" else -1.0
    distance = max(float(target_distance), market.point)
    return TargetStructure(
        ready=0,
        method="legacy_fallback",
        direction=direction,
        impulse_start=0.0,
        impulse_end=0.0,
        impulse_range=0.0,
        impulse_atr=0.0,
        tp1=entry + sign * distance * 0.5,
        tp2=entry + sign * distance,
        tp3=entry + sign * distance * 1.5,
        legacy_target=entry + sign * distance,
    )


def build_target_structure(
    market: Market,
    *,
    direction: str,
    atr: float,
    target_distance: float,
    lookback: int = 12,
    minimum_impulse_atr: float = 0.75,
) -> TargetStructure:
    """Build TP1/TP2/TP3 candidates without affecting the trading decision.

    BUY treats the latest completed downward leg as the retracement reference:
    TP1 is 50% back through that leg, TP2 is its origin, TP3 is a 50% extension.
    SELL mirrors the same construction on the latest completed upward leg.
    """

    side = str(direction).upper()
    if side not in {"BUY", "SELL"} or atr <= 0 or len(market.bars) < 3:
        return _fallback(market, side if side in {"BUY", "SELL"} else "WAIT", target_distance)

    window = market.bars[-max(3, int(lookback)) :]

    if side == "BUY":
        end_index = min(range(1, len(window)), key=lambda i: window[i].low)
        if end_index < 1:
            return _fallback(market, side, target_distance)
        origin_index = max(range(end_index), key=lambda i: window[i].high)
        impulse_start = float(window[origin_index].high)
        impulse_end = float(window[end_index].low)
        impulse_range = impulse_start - impulse_end
        if impulse_range < minimum_impulse_atr * atr:
            return _fallback(market, side, target_distance)
        tp1 = impulse_end + 0.5 * impulse_range
        tp2 = impulse_start
        tp3 = impulse_start + 0.5 * impulse_range
        entry = market.ask
        if not (entry < tp1 < tp2 < tp3):
            return _fallback(market, side, target_distance)
    else:
        end_index = max(range(1, len(window)), key=lambda i: window[i].high)
        if end_index < 1:
            return _fallback(market, side, target_distance)
        origin_index = min(range(end_index), key=lambda i: window[i].low)
        impulse_start = float(window[origin_index].low)
        impulse_end = float(window[end_index].high)
        impulse_range = impulse_end - impulse_start
        if impulse_range < minimum_impulse_atr * atr:
            return _fallback(market, side, target_distance)
        tp1 = impulse_end - 0.5 * impulse_range
        tp2 = impulse_start
        tp3 = impulse_start - 0.5 * impulse_range
        entry = market.bid
        if not (entry > tp1 > tp2 > tp3):
            return _fallback(market, side, target_distance)

    sign = 1.0 if side == "BUY" else -1.0
    legacy_target = (market.ask if side == "BUY" else market.bid) + sign * max(
        float(target_distance), market.point
    )
    return TargetStructure(
        ready=1,
        method="counter_impulse_retrace",
        direction=side,
        impulse_start=impulse_start,
        impulse_end=impulse_end,
        impulse_range=impulse_range,
        impulse_atr=impulse_range / atr,
        tp1=tp1,
        tp2=tp2,
        tp3=tp3,
        legacy_target=legacy_target,
    )
