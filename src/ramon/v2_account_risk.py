"""Ramon V2 redesign: deterministic, fail-closed account-level entry risk gate.

Integration contract: MT5 EA MUST enforce its own broker-side preflight before
OrderSend. This is an independently testable policy kernel, not a live order API.

All risks are denominated in *account units*, never implicitly USD.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable


def _positive(value: float, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a positive finite number")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{field} must be a positive finite number") from exc
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{field} must be a positive finite number")
    return result


@dataclass(frozen=True)
class Exposure:
    """Open or pending exposure; estimate is risk at SL in account units."""
    key: str
    symbol: str
    side: str
    risk_units: float
    volume: float
    has_stop: bool = True
    is_ramon: bool = True

    def validate(self) -> None:
        if not self.key or not self.symbol or self.side not in ("BUY", "SELL"):
            raise ValueError("Invalid exposure identifier, symbol, or side")
        _positive(self.risk_units, "risk_units")
        _positive(self.volume, "volume")
        if not self.has_stop:
            raise ValueError("Open exposure without a validated protective SL")


@dataclass(frozen=True)
class RiskPolicy:
    max_portfolio_risk_units: float
    max_symbol_risk_units: float
    max_side_risk_units: float
    max_positions: int
    minimum_post_trade_margin_level_pct: float = 300.0
    minimum_post_trade_free_margin_units: float = 0.0

    def validate(self) -> None:
        _positive(self.max_portfolio_risk_units, "max_portfolio_risk_units")
        _positive(self.max_symbol_risk_units, "max_symbol_risk_units")
        _positive(self.max_side_risk_units, "max_side_risk_units")
        if isinstance(self.max_positions, bool) or not isinstance(self.max_positions, int) or self.max_positions < 1:
            raise ValueError("max_positions must be >= 1")
        _positive(self.minimum_post_trade_margin_level_pct, "minimum_post_trade_margin_level_pct")
        if not math.isfinite(self.minimum_post_trade_free_margin_units) or self.minimum_post_trade_free_margin_units < 0:
            raise ValueError("minimum post-trade free margin must be nonnegative")


@dataclass(frozen=True)
class AccountSnapshot:
    """EA-sourced complete account snapshot; values must be broker-current."""
    all_positions_included: bool
    pending_orders_included: bool
    mt5_connected: bool
    stopout_lock: bool
    expected_post_trade_margin_level_pct: float | None
    expected_post_trade_free_margin_units: float | None
    margin_check_success: bool
    snapshot_age_seconds: float = 0.0


def check_entry(
    proposed: Exposure,
    positions: Iterable[Exposure],
    policy: RiskPolicy,
    account: AccountSnapshot,
    *,
    maximum_snapshot_age_seconds: float = 5.0,
) -> dict:
    """Reject on unknown account exposure, stale snapshot, SL or margin status.

    Positions MUST include every open position and pending order whose worst
    case risk could affect the account, not just one EA version or Magic.
    Neither netting nor hedging offsets risk in this conservative policy.
    """
    policy.validate()
    reasons = []
    try:
        proposed.validate()
        current = tuple(positions)
        for row in current:
            row.validate()
    except (TypeError, ValueError) as exc:
        return {"allowed": False, "reasons": ["invalid_exposure"],
                "detail": str(exc), "live_order_access": False}
    if len({row.key for row in current}) != len(current) or proposed.key in {row.key for row in current}:
        reasons.append("duplicate_exposure_key")
    if not account.mt5_connected or not account.all_positions_included or not account.pending_orders_included:
        reasons.append("incomplete_account_snapshot")
    if (not math.isfinite(account.snapshot_age_seconds)
            or account.snapshot_age_seconds < 0
            or account.snapshot_age_seconds > maximum_snapshot_age_seconds):
        reasons.append("stale_account_snapshot")
    if account.stopout_lock:
        reasons.append("stopout_lock")
    portfolio_risk = sum(row.risk_units for row in current) + proposed.risk_units
    symbol_risk = sum(row.risk_units for row in current if row.symbol == proposed.symbol) + proposed.risk_units
    side_risk = sum(row.risk_units for row in current
                    if row.symbol == proposed.symbol and row.side == proposed.side) + proposed.risk_units
    eps = 1e-9
    if len(current) + 1 > policy.max_positions:
        reasons.append("position_count_limit")
    if portfolio_risk > policy.max_portfolio_risk_units + eps:
        reasons.append("portfolio_risk_limit")
    if symbol_risk > policy.max_symbol_risk_units + eps:
        reasons.append("symbol_risk_limit")
    if side_risk > policy.max_side_risk_units + eps:
        reasons.append("direction_risk_limit")
    if not account.margin_check_success:
        reasons.append("margin_check_failed")
    margin_level = account.expected_post_trade_margin_level_pct
    free_margin = account.expected_post_trade_free_margin_units
    if margin_level is None or not math.isfinite(margin_level) or margin_level < policy.minimum_post_trade_margin_level_pct:
        reasons.append("margin_level_below_floor")
    if free_margin is None or not math.isfinite(free_margin) or free_margin < policy.minimum_post_trade_free_margin_units:
        reasons.append("free_margin_below_floor")
    return {
        "allowed": not reasons,
        "reasons": reasons,
        "account_risk_units": round(portfolio_risk, 8),
        "symbol_risk_units": round(symbol_risk, 8),
        "side_risk_units": round(side_risk, 8),
        "positions_after": len(current) + 1,
        "live_order_access": False,
    }
