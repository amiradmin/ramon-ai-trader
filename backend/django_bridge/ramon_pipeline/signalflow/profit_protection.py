"""Pure profit-protection rules. No broker access or implicit activation.

Distances use the ORIGINAL entry/stop risk. Callers must retain it after moving
the stop. A proposed stop never loosens protection or changes the target.
"""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class ProfitProtection:
    breakeven_trigger_r: float = 1.0
    breakeven_offset_r: float = 0.0
    trailing_trigger_r: float | None = None
    trailing_distance_r: float = 0.5

    def __post_init__(self):
        values = (self.breakeven_trigger_r, self.breakeven_offset_r, self.trailing_distance_r)
        if not all(math.isfinite(v) for v in values) or self.breakeven_trigger_r <= 0:
            raise ValueError("finite values and a positive breakeven trigger required")
        if not 0 <= self.breakeven_offset_r < self.breakeven_trigger_r or self.trailing_distance_r <= 0:
            raise ValueError("invalid offset or trailing distance")
        if self.trailing_trigger_r is not None and (
            not math.isfinite(self.trailing_trigger_r) or self.trailing_trigger_r <= 0
        ):
            raise ValueError("positive finite trailing trigger required")

    def stop(self, *, side: str, entry: float, original_risk: float,
             current_stop: float, favorable_price: float) -> float:
        """Use executable bid for BUY / ask for SELL, or a completed-bar extreme.

        OHLC simulations must apply this result only on the following bar, since
        the current bar's high/low ordering is unknown. Breakeven is a price level,
        not a guarantee of zero net P&L after gaps, fees and execution costs.
        """
        if side not in {"BUY", "SELL"}:
            raise ValueError("BUY or SELL required")
        if not all(math.isfinite(v) and v > 0 for v in
                   (entry, original_risk, current_stop, favorable_price)):
            raise ValueError("positive finite prices and original risk required")
        sign = 1 if side == "BUY" else -1
        excursion = sign * (favorable_price-entry) / original_risk
        proposed = current_stop
        if excursion >= self.breakeven_trigger_r:
            candidate = entry + sign*self.breakeven_offset_r*original_risk
            if sign*(candidate-proposed) > 0:
                proposed = candidate
        if self.trailing_trigger_r is not None and excursion >= self.trailing_trigger_r:
            candidate = favorable_price-sign*self.trailing_distance_r*original_risk
            if sign*(candidate-proposed) > 0:
                proposed = candidate
        return proposed
