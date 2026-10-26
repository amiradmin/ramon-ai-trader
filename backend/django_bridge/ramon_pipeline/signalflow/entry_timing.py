"""Stage 2: decide whether an approved opportunity is timely enough to enter.

This stage evaluates timing only. It does not create orders, choose stops or
targets, size positions, or keep state. The executor must enforce idempotency.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any, Mapping

ACCEPTED = "ACCEPTED"
REJECTED = "REJECTED"
DEFERRED = "DEFERRED"

_TIMEFRAME_SECONDS = {
    "M1": 60, "M2": 120, "M3": 180, "M4": 240, "M5": 300, "M6": 360,
    "M10": 600, "M12": 720, "M15": 900, "M20": 1200, "M30": 1800,
    "H1": 3600, "H2": 7200, "H3": 10800, "H4": 14400, "H6": 21600,
    "H8": 28800, "H12": 43200, "D1": 86400, "W1": 604800,
}


@dataclass(frozen=True)
class EntryTimingConfig:
    """Quote-aware limits; defaults are conservative starting points, not optimized values."""
    max_signal_age_bars: float = 1.0
    max_chase_atr: float = 0.5
    min_risk_room_atr: float = 0.25
    max_spread_atr: float = 0.20
    max_spread_to_risk: float = 0.25

    def __post_init__(self) -> None:
        values = {
            "max_signal_age_bars": self.max_signal_age_bars,
            "max_chase_atr": self.max_chase_atr,
            "min_risk_room_atr": self.min_risk_room_atr,
            "max_spread_atr": self.max_spread_atr,
            "max_spread_to_risk": self.max_spread_to_risk,
        }
        for name, value in values.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{name} must be finite and numeric")
        if self.max_signal_age_bars < 0:
            raise ValueError("max_signal_age_bars must not be negative")
        for name in ("max_chase_atr", "min_risk_room_atr", "max_spread_atr", "max_spread_to_risk"):
            if values[name] <= 0:
                raise ValueError(f"{name} must be positive")


class _Invalid(ValueError):
    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason, self.detail = reason, detail


def _number(value: Any, name: str, reason: str = "INVALID_ENTRY_METRICS") -> float:
    if isinstance(value, bool):
        raise _Invalid(reason, f"{name} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise _Invalid(reason, f"{name} must be numeric") from None
    if not math.isfinite(number):
        raise _Invalid(reason, f"{name} must be finite")
    return number


def _clip(value: float) -> float:
    return max(0.0, min(1.0, value))


def _timestamp(value: Any) -> float:
    """Convert epoch seconds or an ISO-8601/datetime value to UTC epoch seconds."""
    if isinstance(value, bool) or value is None:
        raise ValueError("timestamp is missing or invalid")
    if isinstance(value, (int, float)):
        result = float(value)
    elif isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        result = dt.timestamp()
    else:
        text = str(value).strip()
        try:
            result = float(text)
        except ValueError:
            normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
            dt = datetime.fromisoformat(normalized)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            result = dt.timestamp()
    if not math.isfinite(result):
        raise ValueError("timestamp must be finite")
    return result


class EntryTimer:
    """Evaluate legacy quality metrics or quote-aware structured setups."""

    def __init__(
        self,
        minimum_quality: float = 0.65,
        maximum_spread_points: float = 50.0,
        *,
        config: EntryTimingConfig | None = None,
        require_quote: bool = False,
    ) -> None:
        if isinstance(minimum_quality, bool) or not math.isfinite(float(minimum_quality)) or not 0 <= minimum_quality <= 1:
            raise ValueError("minimum_quality must be between 0 and 1")
        if isinstance(maximum_spread_points, bool) or not math.isfinite(float(maximum_spread_points)) or maximum_spread_points <= 0:
            raise ValueError("maximum_spread_points must be finite and positive")
        self.minimum_quality = float(minimum_quality)
        self.maximum_spread_points = float(maximum_spread_points)
        self.config = config or EntryTimingConfig()
        self.require_quote = require_quote

    def evaluate(self, opportunity: Mapping[str, Any], market: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(opportunity, Mapping) or not opportunity.get("approved"):
            return self._result(REJECTED, "NO_APPROVED_OPPORTUNITY", detail="no approved opportunity to time")
        if not isinstance(market, Mapping):
            return self._result(REJECTED, "INVALID_ENTRY_METRICS", detail="market snapshot must be a mapping")
        try:
            setup = opportunity.get("setup")
            return self._evaluate_structured(opportunity, setup, market) if isinstance(setup, Mapping) else self._evaluate_legacy(opportunity, market)
        except _Invalid as exc:
            return self._result(REJECTED, exc.reason, detail=exc.detail, opportunity=opportunity)

    @staticmethod
    def _result(status: str, reason: str, *, detail: str = "", opportunity: Mapping[str, Any] | None = None, **fields: Any) -> dict[str, Any]:
        approved = status == ACCEPTED
        result: dict[str, Any] = {
            "approved": approved,
            "status": status,
            "timing": "ENTER_NOW" if approved else "WAIT" if status == DEFERRED else "SKIP",
            "reason": reason,
            "explanation": fields.pop("explanation", None) or f"entry {'approved' if approved else 'not approved'} ({status.lower()}): {detail or reason}",
        }
        if opportunity is not None:
            signal_id = opportunity.get("signal_id") or opportunity.get("signal_key")
            result["side"] = opportunity.get("side", "HOLD") if approved else "HOLD"
            result["signal_id"] = signal_id
            result["idempotency_key"] = f"entry:{signal_id}" if signal_id else None
        result.update(fields)
        return result

    def _evaluate_legacy(self, opportunity: Mapping[str, Any], market: Mapping[str, Any]) -> dict[str, Any]:
        try:
            quality = _number(market.get("entry_quality", 0.0), "entry_quality")
            raw_spread = market.get("spread_points", float("inf"))
            if isinstance(raw_spread, bool):
                raise ValueError("spread_points must be numeric")
            spread = float(raw_spread)
            if math.isnan(spread):
                raise ValueError("spread_points must not be NaN")
        except (_Invalid, TypeError, ValueError, OverflowError):
            return self._result(REJECTED, "INVALID_ENTRY_METRICS", detail="entry_quality and spread_points must be numeric and not NaN", opportunity=opportunity)
        metrics = {"quality": quality, "spread_points": spread}
        if quality < self.minimum_quality or quality > 1.0:
            return self._result(DEFERRED, "ENTRY_QUALITY_TOO_LOW", opportunity=opportunity, **metrics,
                                detail=f"entry quality {quality:.2f} is outside {self.minimum_quality:.2f}..1.00")
        if spread < 0 or spread > self.maximum_spread_points:
            return self._result(DEFERRED, "SPREAD_OUT_OF_RANGE", opportunity=opportunity, **metrics,
                                detail=f"spread {spread:g} points exceeds the {self.maximum_spread_points:g} point limit")
        return self._result(ACCEPTED, "ENTRY_TIMING_APPROVED", opportunity=opportunity, **metrics,
                            detail=f"entry quality {quality:.2f} and spread {spread:g} points are acceptable")

    def _evaluate_structured(self, opportunity: Mapping[str, Any], setup: Mapping[str, Any], market: Mapping[str, Any]) -> dict[str, Any]:
        cfg = self.config
        checks: list[dict[str, Any]] = []

        def check(name: str, passed: bool, detail: str) -> None:
            checks.append({"name": name, "passed": passed, "detail": detail})

        side = str(opportunity.get("side", "")).upper()
        if side not in {"BUY", "SELL"}:
            raise _Invalid("INVALID_OPPORTUNITY_SETUP", "approved opportunity has no BUY/SELL side")
        sign = 1 if side == "BUY" else -1
        try:
            atr = _number(setup["atr"], "setup.atr", "INVALID_OPPORTUNITY_SETUP")
            reference = _number(setup["reference_price"], "setup.reference_price", "INVALID_OPPORTUNITY_SETUP")
            invalidation = _number(setup["invalidation_price"], "setup.invalidation_price", "INVALID_OPPORTUNITY_SETUP")
        except KeyError as exc:
            raise _Invalid("INVALID_OPPORTUNITY_SETUP", f"setup is missing {exc.args[0]}") from None
        if atr <= 0 or reference <= 0 or invalidation <= 0:
            raise _Invalid("INVALID_OPPORTUNITY_SETUP", "setup prices and ATR must be positive")
        if (reference - invalidation) * sign <= 0:
            raise _Invalid("INVALID_OPPORTUNITY_SETUP", "invalidation level is on the wrong side of reference price")

        quote = self._read_quote(market)
        if quote is None and self.require_quote:
            raise _Invalid("QUOTE_REQUIRED", "bid, ask and point are required for entry timing")
        if quote is None:
            spread = _number(market.get("spread_points", float("inf")), "spread_points")
            entry = exit_side = point = None
        else:
            bid, ask, point = quote
            spread = (ask - bid) / point
            entry, exit_side = (ask, bid) if sign > 0 else (bid, ask)
        metrics: dict[str, Any] = {"spread_points": spread, "price_checks": "APPLIED" if quote else "SKIPPED_NO_QUOTE"}
        if quote:
            metrics["quote_price"] = entry
        if spread < 0 or spread > self.maximum_spread_points:
            check("spread_absolute", False, "spread exceeds the absolute limit")
            return self._finish(DEFERRED, "SPREAD_OUT_OF_RANGE", opportunity, checks, metrics, f"spread {spread:g} points exceeds {self.maximum_spread_points:g}")
        check("spread_absolute", True, f"spread {spread:g} points is within the absolute limit")

        age = self._signal_age_bars(opportunity, market)
        age_quality = 0.0
        if age is not None:
            metrics["signal_age_bars"] = round(age, 3)
            if age < 0:
                check("freshness", False, "signal bar is newer than the latest supplied bar")
                return self._finish(REJECTED, "SIGNAL_TIME_INVALID", opportunity, checks, metrics, "possible look-ahead: signal time is in the future")
            if age > cfg.max_signal_age_bars:
                check("freshness", False, f"signal is {age:.2f} bars old")
                return self._finish(REJECTED, "SIGNAL_EXPIRED", opportunity, checks, metrics, f"signal age exceeds {cfg.max_signal_age_bars:g} bars")
            check("freshness", True, f"signal is {age:.2f} bars old")
            age_quality = age
        else:
            check("freshness", True, "skipped: signal and latest bar timestamps were not both supplied")

        chase_atr = spread_atr = 0.0
        if quote:
            if (exit_side - invalidation) * sign <= 0:
                check("invalidation", False, "market has traded through the invalidation level")
                return self._finish(REJECTED, "SETUP_INVALIDATED", opportunity, checks, metrics, f"price has traded through invalidation {invalidation:g}")
            room_atr = (entry - invalidation) * sign / atr
            metrics["room_to_invalidation_atr"] = round(room_atr, 3)
            if room_atr < cfg.min_risk_room_atr:
                check("invalidation", False, f"only {room_atr:.2f} ATR remains")
                return self._finish(REJECTED, "TOO_CLOSE_TO_INVALIDATION", opportunity, checks, metrics, "entry is too close to invalidation")
            check("invalidation", True, f"{room_atr:.2f} ATR remains to invalidation")

            chase_atr = (entry - reference) * sign / atr
            metrics["chase_atr"] = round(chase_atr, 3)
            if chase_atr > cfg.max_chase_atr:
                check("chase", False, f"price ran {chase_atr:.2f} ATR beyond reference")
                return self._finish(DEFERRED, "PRICE_EXTENDED_FROM_SIGNAL", opportunity, checks, metrics, "price has run away; wait for a retracement")
            check("chase", True, f"price is {chase_atr:+.2f} ATR from reference")

            spread_atr = spread * point / atr
            metrics["spread_atr_fraction"] = round(spread_atr, 4)
            if spread_atr > cfg.max_spread_atr:
                check("spread_volatility", False, "spread is too wide relative to ATR")
                return self._finish(DEFERRED, "SPREAD_TOO_WIDE_FOR_VOLATILITY", opportunity, checks, metrics, "spread is too wide for current volatility")
            risk_distance = (entry - invalidation) * sign
            spread_risk = spread * point / risk_distance
            metrics["spread_to_risk"] = round(spread_risk, 4)
            if spread_risk > cfg.max_spread_to_risk:
                check("spread_risk", False, "spread is too large relative to invalidation distance")
                return self._finish(DEFERRED, "SPREAD_TOO_LARGE_VS_RISK", opportunity, checks, metrics, "spread is too large relative to risk distance")
            check("spread_volatility", True, "spread is acceptable relative to ATR and risk distance")
        else:
            check("price_checks", True, "price-based checks skipped because no live quote was supplied")

        if market.get("entry_quality") is not None and "entry_quality" in market:
            quality = _number(market["entry_quality"], "entry_quality")
            source = "SUPPLIED"
        else:
            freshness = 1.0 - age_quality / (cfg.max_signal_age_bars + 1.0)
            spread_eff = 1.0 - 0.5 * _clip(spread_atr / cfg.max_spread_atr) if quote else 0.75
            location = 1.0 - 0.5 * _clip(max(chase_atr, 0.0) / cfg.max_chase_atr) if quote else 0.75
            quality = 0.4 * spread_eff + 0.35 * location + 0.25 * freshness
            source = "COMPUTED"
        metrics.update(quality=round(quality, 4), quality_source=source)
        if not 0 <= quality <= 1 or quality < self.minimum_quality:
            check("quality", False, f"quality {quality:.2f} is below the minimum")
            return self._finish(DEFERRED, "ENTRY_QUALITY_TOO_LOW", opportunity, checks, metrics, f"entry quality {quality:.2f} is below {self.minimum_quality:.2f}")
        check("quality", True, f"quality {quality:.2f} ({source.lower()})")
        detail = f"{side} entry timing approved; spread {spread:g} points, quality {quality:.2f}"
        if quote:
            detail += f", price {chase_atr:+.2f} ATR from reference"
        return self._finish(ACCEPTED, "ENTRY_TIMING_APPROVED", opportunity, checks, metrics, detail)

    def _finish(self, status: str, reason: str, opportunity: Mapping[str, Any], checks: list[dict[str, Any]], metrics: Mapping[str, Any], detail: str) -> dict[str, Any]:
        fields = {**metrics, "checks": checks}
        if status == ACCEPTED:
            fields["explanation"] = detail
        return self._result(status, reason, detail=detail, opportunity=opportunity, **fields)

    @staticmethod
    def _read_quote(market: Mapping[str, Any]) -> tuple[float, float, float] | None:
        keys = ("bid", "ask", "point")
        if all(market.get(key) is None for key in keys):
            return None
        try:
            bid = _number(market.get("bid"), "bid", "INVALID_QUOTE")
            ask = _number(market.get("ask"), "ask", "INVALID_QUOTE")
            point = _number(market.get("point"), "point", "INVALID_QUOTE")
        except _Invalid:
            raise _Invalid("INVALID_QUOTE", "bid, ask and point must all be supplied as finite numbers") from None
        if bid <= 0 or ask < bid or point <= 0:
            raise _Invalid("INVALID_QUOTE", "quote must satisfy 0 < bid <= ask and point > 0")
        return bid, ask, point

    @staticmethod
    def _signal_age_bars(opportunity: Mapping[str, Any], market: Mapping[str, Any]) -> float | None:
        signal_time = opportunity.get("signal_bar_time")
        if signal_time is None:
            return None
        latest = market.get("latest_bar_time")
        bars = market.get("bars")
        if latest is None and isinstance(bars, (list, tuple)) and bars and isinstance(bars[-1], Mapping):
            latest = bars[-1].get("time")
        if latest is None:
            return None
        try:
            signal, latest_time = _timestamp(signal_time), _timestamp(latest)
        except (TypeError, ValueError, OverflowError) as exc:
            raise _Invalid("SIGNAL_TIME_INVALID", str(exc)) from None
        interval = _TIMEFRAME_SECONDS.get(str(market.get("timeframe", "M15")).upper(), market.get("bar_interval_seconds"))
        if isinstance(interval, bool) or not isinstance(interval, (int, float)) or not math.isfinite(float(interval)) or interval <= 0:
            return None
        return (latest_time - signal) / float(interval)
