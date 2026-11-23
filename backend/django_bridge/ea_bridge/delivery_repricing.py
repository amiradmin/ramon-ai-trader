"""Fail-closed, opt-in risk-aware repricing of approved strategy OPEN orders.

No broker access. The caller supplies a *fresh, scoped* EA snapshot, and must
run its ordinary account, news, exposure and broker authorization checks.
A change may only reduce the original approved volume. No SL or TP widening.
"""
from decimal import Decimal, InvalidOperation, ROUND_FLOOR


class DeliveryRiskError(ValueError):
    """Stable rejection reason suitable for delivery audit."""


def _d(value, name):
    if isinstance(value, bool):
        raise DeliveryRiskError("DELIVERY_INVALID_" + name.upper())
    try:
        result = Decimal(str(value))
    except (ValueError, InvalidOperation, TypeError):
        raise DeliveryRiskError("DELIVERY_INVALID_" + name.upper()) from None
    if not result.is_finite():
        raise DeliveryRiskError("DELIVERY_INVALID_" + name.upper())
    return result


def reprice_strategy_open(plan, quote, spec, *, approved_risk_money,
                          account_balance, min_reward_risk=Decimal("1.2")):
    """Return (adjusted payload, diagnostics), without changing input data.

    Handles quote movement only; no change to direction, SL, TP, account or
    signal. Refuses sub-minimum volume and inadequately rewarded signals.
    """
    side = plan.get("side")
    if side not in ("BUY", "SELL"):
        raise DeliveryRiskError("DELIVERY_INVALID_SIDE")
    point = _d(quote.get("point"), "point")
    bid, ask = _d(quote.get("bid"), "bid"), _d(quote.get("ask"), "ask")
    tick_size = _d(spec.get("tick_size"), "tick_size")
    tick_value = _d(spec.get("tick_value"), "tick_value")
    volume_min = _d(spec.get("volume_min"), "volume_min")
    volume_max = _d(spec.get("volume_max"), "volume_max")
    volume_step = _d(spec.get("volume_step"), "volume_step")
    stops = _d(spec.get("stops_level_points"), "stops_level_points") * point
    original_volume = _d(plan.get("volume"), "volume")
    sl, tp = _d(plan.get("stop_loss"), "stop_loss"), _d(plan.get("take_profit"), "take_profit")
    budget = _d(approved_risk_money, "approved_risk_money")
    balance = _d(account_balance, "account_balance")
    required_rr = _d(min_reward_risk, "min_reward_risk")
    values = (point, tick_size, tick_value, volume_min, volume_step, original_volume,
              budget, balance, required_rr)
    if any(x <= 0 for x in values) or volume_max < volume_min or stops < 0 or bid <= 0 or ask < bid:
        raise DeliveryRiskError("DELIVERY_INVALID_MARKET_METADATA")
    # The origin command's volume must itself satisfy broker step limits.
    def on_grid(v):
        return v >= volume_min and v <= volume_max and (v - volume_min) % volume_step == 0
    if not on_grid(original_volume):
        raise DeliveryRiskError("DELIVERY_INVALID_ORIGINAL_VOLUME")
    entry = ask if side == "BUY" else bid
    risk_distance = (entry - sl) if side == "BUY" else (sl - entry)
    reward_distance = (tp - entry) if side == "BUY" else (entry - tp)
    if min(risk_distance, reward_distance) <= 0:
        raise DeliveryRiskError("DELIVERY_INVALID_STOP_TARGET")
    if risk_distance <= stops or reward_distance <= stops:
        raise DeliveryRiskError("DELIVERY_BROKER_STOP_DISTANCE")
    rr = reward_distance / risk_distance
    if rr < required_rr:
        raise DeliveryRiskError("DELIVERY_REWARD_RISK_TOO_LOW")
    # Never exceed original approved risk or existing absolute per-trade cap.
    ceiling = min(budget, balance * Decimal("0.005"))
    risk_per_lot = risk_distance / tick_size * tick_value
    allowed = ceiling / risk_per_lot
    if allowed < volume_min:
        raise DeliveryRiskError("DELIVERY_MINIMUM_LOT_EXCEEDS_RISK")
    steps = ((min(allowed, original_volume) - volume_min) / volume_step).to_integral_value(rounding=ROUND_FLOOR)
    volume = volume_min + steps * volume_step
    if not on_grid(volume) or volume > original_volume:
        raise DeliveryRiskError("DELIVERY_VOLUME_GRID_INVALID")
    actual_risk = risk_per_lot * volume
    if actual_risk > ceiling:
        raise DeliveryRiskError("DELIVERY_APPROVED_RISK_EXCEEDED")
    updated = dict(plan, volume=float(volume))
    return updated, {
        "policy": "RISK_AWARE_DELIVERY_V1",
        "original_volume": str(original_volume),
        "delivery_volume": str(volume),
        "approved_risk_money": str(budget),
        "delivery_risk_money": str(actual_risk),
        "effective_ceiling_money": str(ceiling),
        "reward_risk": str(rr),
        "volume_reduced": volume < original_volume,
        "no_stop_target_changes": True,
    }
