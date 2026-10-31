"""Stage 2: decide whether an approved opportunity is timely enough to enter *now*.

Opportunities (stage 1) decides *what* deserves attention; this stage only judges
*when*. For a structured opportunity (one carrying a ``setup``) and a live quote it
checks that:

* the signal is still fresh and was not produced from the future,
* the setup has not been invalidated by price,
* price has not run away from the signal (no chasing),
* the spread is acceptable in absolute terms, against current volatility (ATR) and
  against the distance to the invalidation level.

Without a ``setup`` (the legacy ``trend``/``setup_score`` flow) the original
``entry_quality`` + ``spread_points`` gates apply unchanged, with the same reason codes.

Result vocabulary matches stage 1: ``status`` is ``ACCEPTED`` / ``REJECTED`` /
``DEFERRED`` (deferred = conditions may improve, try again on a later tick) and
``timing`` is ``ENTER_NOW`` / ``WAIT`` / ``SKIP``. ``approved`` stays the field the
pipeline reads. Approval is a verdict only: this stage chooses no order type, price,
stop, target or volume (``quote_price`` is the quote the verdict was based on), and it
keeps no state. The executor must de-duplicate on ``idempotency_key`` so that
re-evaluating the same signal on every tick never produces two orders.
"""
import math
from dataclasses import dataclass
from typing import Any, Mapping

from .common import ACCEPTED, DEFERRED, REJECTED, InvalidMarketData, clip, parse_timestamp

_TIMEFRAME_SECONDS = {
    "M1": 60, "M2": 120, "M3": 180, "M4": 240, "M5": 300, "M6": 360, "M10": 600, "M12": 720,
    "M15": 900, "M20": 1200, "M30": 1800, "H1": 3600, "H2": 7200, "H3": 10800, "H4": 14400,
    "H6": 21600, "H8": 28800, "H12": 43200, "D1": 86400, "W1": 604800,
}


@dataclass(frozen=True)
class EntryTimingConfig:
    """Thresholds for quote-aware timing. Distances are in ATR (taken from the setup).

    Defaults are conventional priors, not values optimised on data.
    """

    max_signal_age_bars: float = 1.0  # completed bars allowed between the signal bar and now
    max_chase_atr: float = 0.5  # how far price may run beyond the signal close
    min_risk_room_atr: float = 0.25  # entry must stay at least this far from the invalidation level
    max_spread_atr: float = 0.20  # spread as a fraction of ATR
    max_spread_to_risk: float = 0.25  # spread as a fraction of the distance to invalidation
    # Validation / safety options. Defaults keep the original behaviour.
    require_signal_time: bool = False  # reject (instead of skipping freshness) when signal/latest bar time is missing
    ignore_supplied_quality: bool = False  # always compute quality from spread/chase/freshness, never take market["entry_quality"]

    def __post_init__(self) -> None:
        if self.max_signal_age_bars < 0:
            raise ValueError("max_signal_age_bars must not be negative")
        for name in ("max_chase_atr", "min_risk_room_atr", "max_spread_atr", "max_spread_to_risk"):
            if not getattr(self, name) > 0:
                raise ValueError(f"{name} must be positive")


class _Invalid(ValueError):
    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def _number(value: Any, name: str, reason: str = "INVALID_ENTRY_METRICS") -> float:
    if isinstance(value, bool):
        raise _Invalid(reason, f"{name} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise _Invalid(reason, f"{name} must be numeric") from None
    if math.isnan(number):
        raise _Invalid(reason, f"{name} must not be NaN")
    return number


class EntryTimer:
    def __init__(
        self,
        minimum_quality: float = 0.65,
        maximum_spread_points: float = 50.0,
        *,
        config: EntryTimingConfig | None = None,
        require_quote: bool = False,
    ) -> None:
        if not 0.0 <= minimum_quality <= 1.0:
            raise ValueError("minimum_quality must be between 0 and 1")
        if maximum_spread_points <= 0:
            raise ValueError("maximum_spread_points must be positive")
        self.minimum_quality = minimum_quality
        self.maximum_spread_points = maximum_spread_points
        self.config = config or EntryTimingConfig()
        # When True, a structured opportunity without bid/ask/point is rejected instead of
        # being judged on the supplied spread alone (price-based checks are then skipped).
        self.require_quote = require_quote

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate(self, opportunity: Mapping[str, Any], market: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(opportunity, Mapping) or not opportunity.get("approved"):
            return self._result(REJECTED, "NO_APPROVED_OPPORTUNITY", detail="no approved opportunity to time")
        if not isinstance(market, Mapping):
            return self._result(REJECTED, "INVALID_ENTRY_METRICS", detail="market snapshot must be a mapping")
        setup = opportunity.get("setup")
        try:
            if isinstance(setup, Mapping):
                return self._evaluate_structured(opportunity, setup, market)
            return self._evaluate_legacy(opportunity, market)
        except _Invalid as exc:
            return self._result(REJECTED, exc.reason, detail=exc.detail, opportunity=opportunity)

    # ------------------------------------------------------------------
    # Result helper
    # ------------------------------------------------------------------

    @staticmethod
    def _result(
        status: str,
        reason: str,
        *,
        detail: str = "",
        opportunity: Mapping[str, Any] | None = None,
        **fields: Any,
    ) -> dict[str, Any]:
        approved = status == ACCEPTED
        explanation = fields.pop("explanation", None) or (
            f"entry {'approved' if approved else 'not approved'} ({status.lower()}): {detail or reason}"
        )
        result: dict[str, Any] = {
            "approved": approved,
            "status": status,
            "timing": "ENTER_NOW" if approved else "WAIT" if status == DEFERRED else "SKIP",
            "reason": reason,
            "explanation": explanation,
        }
        if opportunity is not None:
            signal_id = opportunity.get("signal_id")
            result["side"] = opportunity.get("side", "HOLD") if approved else "HOLD"
            result["signal_id"] = signal_id
            result["idempotency_key"] = f"entry:{signal_id}" if signal_id else None
        result.update(fields)
        return result

    # ------------------------------------------------------------------
    # Legacy flow: entry_quality and spread_points supplied by the caller
    # ------------------------------------------------------------------

    def _evaluate_legacy(self, opportunity: Mapping[str, Any], market: Mapping[str, Any]) -> dict[str, Any]:
        try:
            quality = float(market.get("entry_quality", 0.0))
            spread = float(market.get("spread_points", float("inf")))
        except (TypeError, ValueError):
            return self._result(REJECTED, "INVALID_ENTRY_METRICS", detail="entry_quality and spread_points must be numeric",
                                opportunity=opportunity)
        if math.isnan(quality) or math.isnan(spread):
            return self._result(REJECTED, "INVALID_ENTRY_METRICS", detail="entry_quality and spread_points must not be NaN",
                                opportunity=opportunity)
        metrics = {"quality": quality, "spread_points": spread}
        if quality < self.minimum_quality or quality > 1.0:
            return self._result(DEFERRED, "ENTRY_QUALITY_TOO_LOW", opportunity=opportunity, **metrics,
                                detail=f"entry quality {quality:.2f} is outside {self.minimum_quality:.2f}..1.00")
        if spread < 0 or spread > self.maximum_spread_points:
            return self._result(DEFERRED, "SPREAD_OUT_OF_RANGE", opportunity=opportunity, **metrics,
                                detail=f"spread {spread:g} points exceeds the {self.maximum_spread_points:g} point limit")
        return self._result(ACCEPTED, "ENTRY_TIMING_APPROVED", opportunity=opportunity, **metrics,
                            detail=f"entry quality {quality:.2f} and spread {spread:g} points are acceptable")

    # ------------------------------------------------------------------
    # Structured flow: quote-aware timing of a setup from opportunities.py
    # ------------------------------------------------------------------

    def _evaluate_structured(
        self, opportunity: Mapping[str, Any], setup: Mapping[str, Any], market: Mapping[str, Any]
    ) -> dict[str, Any]:
        cfg = self.config
        checks: list[dict[str, Any]] = []

        def check(name: str, passed: bool, detail: str) -> bool:
            checks.append({"name": name, "passed": passed, "detail": detail})
            return passed

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
        if atr <= 0 or reference <= 0 or invalidation <= 0 or not all(map(math.isfinite, (atr, reference, invalidation))):
            raise _Invalid("INVALID_OPPORTUNITY_SETUP", "setup prices and ATR must be positive and finite")
        if (reference - invalidation) * sign <= 0:
            raise _Invalid("INVALID_OPPORTUNITY_SETUP", "invalidation level is on the wrong side of the reference price")

        quote = self._read_quote(market)
        if quote is None and self.require_quote:
            raise _Invalid("QUOTE_REQUIRED", "bid, ask and point are required for entry timing")
        if quote is not None:
            bid, ask, point = quote
            spread = (ask - bid) / point
            entry = ask if sign > 0 else bid  # price a market order would fill at
            exit_side = bid if sign > 0 else ask  # price that would close it again
        else:
            spread = _number(market.get("spread_points", float("inf")), "spread_points")
            point = entry = exit_side = None
        metrics: dict[str, Any] = {"spread_points": spread, "price_checks": "APPLIED" if quote else "SKIPPED_NO_QUOTE"}
        if quote is not None:
            metrics["quote_price"] = entry
        if spread < 0 or spread > self.maximum_spread_points:
            check("spread_absolute", False, f"spread {spread:g} points exceeds the {self.maximum_spread_points:g} point limit")
            return self._finish(DEFERRED, "SPREAD_OUT_OF_RANGE", opportunity, checks, metrics,
                                f"spread {spread:g} points exceeds the {self.maximum_spread_points:g} point limit")
        check("spread_absolute", True, f"spread {spread:g} points within {self.maximum_spread_points:g}")

        # Freshness -----------------------------------------------------------
        age = self._signal_age_bars(opportunity, market)
        if age is None and cfg.require_signal_time:
            check("freshness", False, "signal or latest bar time not supplied")
            return self._finish(REJECTED, "SIGNAL_TIME_UNAVAILABLE", opportunity, checks, metrics,
                                "signal or latest bar time not supplied, so freshness cannot be judged")
        if age is None:
            check("freshness", True, "skipped: signal or latest bar time not supplied")
            age_for_quality = 0.0
        else:
            metrics["signal_age_bars"] = round(age, 3)
            if age < 0:
                check("freshness", False, "the signal bar is newer than the latest bar supplied")
                return self._finish(REJECTED, "SIGNAL_TIME_INVALID", opportunity, checks, metrics,
                                    "the signal bar is newer than the latest bar supplied (possible look-ahead)")
            if age > cfg.max_signal_age_bars:
                detail = f"signal is {age:.1f} bars old (maximum {cfg.max_signal_age_bars:g})"
                check("freshness", False, detail)
                return self._finish(REJECTED, "SIGNAL_EXPIRED", opportunity, checks, metrics, detail)
            check("freshness", True, f"signal is {age:.1f} bar(s) old")
            age_for_quality = age

        # Price checks (need a live quote) -----------------------------------------
        chase_atr = 0.0
        spread_atr_fraction = 0.0
        if quote is not None:
            if (exit_side - invalidation) * sign <= 0:
                detail = f"price has already traded through the invalidation level {invalidation:g}"
                check("invalidation", False, detail)
                return self._finish(REJECTED, "SETUP_INVALIDATED", opportunity, checks, metrics, detail)
            room_atr = (entry - invalidation) * sign / atr
            metrics["room_to_invalidation_atr"] = round(room_atr, 3)
            if room_atr < cfg.min_risk_room_atr:
                detail = f"entry is only {room_atr:.2f} ATR from the invalidation level (minimum {cfg.min_risk_room_atr})"
                check("invalidation", False, detail)
                return self._finish(REJECTED, "TOO_CLOSE_TO_INVALIDATION", opportunity, checks, metrics, detail)
            check("invalidation", True, f"{room_atr:.2f} ATR of room to the invalidation level")

            chase_atr = (entry - reference) * sign / atr
            metrics["chase_atr"] = round(chase_atr, 3)
            if chase_atr > cfg.max_chase_atr:
                detail = f"price has run {chase_atr:.2f} ATR beyond the signal close (maximum {cfg.max_chase_atr}); wait for a retrace"
                check("chase", False, detail)
                return self._finish(DEFERRED, "PRICE_EXTENDED_FROM_SIGNAL", opportunity, checks, metrics, detail)
            check("chase", True, f"price is {chase_atr:+.2f} ATR from the signal close")

            spread_price = spread * point
            spread_atr_fraction = spread_price / atr
            metrics["spread_atr_fraction"] = round(spread_atr_fraction, 3)
            if spread_atr_fraction > cfg.max_spread_atr:
                detail = f"spread is {spread_atr_fraction:.0%} of ATR (maximum {cfg.max_spread_atr:.0%})"
                check("spread_volatility", False, detail)
                return self._finish(DEFERRED, "SPREAD_TOO_WIDE_FOR_VOLATILITY", opportunity, checks, metrics, detail)
            distance = (entry - invalidation) * sign
            spread_risk = spread_price / distance
            metrics["spread_to_risk"] = round(spread_risk, 3)
            if spread_risk > cfg.max_spread_to_risk:
                detail = f"spread is {spread_risk:.0%} of the distance to invalidation (maximum {cfg.max_spread_to_risk:.0%})"
                check("spread_risk", False, detail)
                return self._finish(DEFERRED, "SPREAD_TOO_LARGE_VS_RISK", opportunity, checks, metrics, detail)
            check("spread_volatility", True, f"spread is {spread_atr_fraction:.0%} of ATR and {spread_risk:.0%} of the risk distance")
        else:
            check("price_checks", True, "skipped: no bid/ask/point supplied, so invalidation, chase and relative spread were not judged")

        # Quality -----------------------------------------------------------------
        if not cfg.ignore_supplied_quality and "entry_quality" in market and market["entry_quality"] is not None:
            quality = _number(market["entry_quality"], "entry_quality")
            source = "SUPPLIED"
        else:
            freshness = 1.0 - age_for_quality / (cfg.max_signal_age_bars + 1.0)
            # Hard limits are enforced by the gates above; quality only ranks what passed them
            # (1.0 at best, 0.5 right at a limit) so it does not double-penalise the same costs.
            spread_eff = 1.0 - 0.5 * clip(spread_atr_fraction / cfg.max_spread_atr) if quote is not None else 0.75
            location = 1.0 - 0.5 * clip(max(chase_atr, 0.0) / cfg.max_chase_atr) if quote is not None else 0.75
            quality = 0.4 * spread_eff + 0.35 * location + 0.25 * freshness
            source = "COMPUTED"
        metrics.update(quality=round(quality, 4), quality_source=source)
        if not 0.0 <= quality <= 1.0 or quality < self.minimum_quality:
            detail = f"entry quality {quality:.2f} is below the minimum {self.minimum_quality:.2f}"
            check("quality", False, detail)
            return self._finish(DEFERRED, "ENTRY_QUALITY_TOO_LOW", opportunity, checks, metrics, detail)
        check("quality", True, f"entry quality {quality:.2f} ({source.lower()})")

        explanation = (
            f"{side} entry timing approved: signal {metrics.get('signal_age_bars', 0):g} bar(s) old, "
            f"spread {spread:g} points, quality {quality:.2f}"
        )
        if quote is not None:
            explanation += (
                f", price {metrics['chase_atr']:+.2f} ATR from the signal close with "
                f"{metrics['room_to_invalidation_atr']:.2f} ATR to invalidation"
            )
        return self._finish(ACCEPTED, "ENTRY_TIMING_APPROVED", opportunity, checks, metrics, explanation)

    def _finish(
        self,
        status: str,
        reason: str,
        opportunity: Mapping[str, Any],
        checks: list[dict[str, Any]],
        metrics: Mapping[str, Any],
        detail: str,
    ) -> dict[str, Any]:
        fields: dict[str, Any] = {**metrics, "checks": checks}
        if status == ACCEPTED:  # for an approval ``detail`` already is the full explanation
            fields["explanation"] = detail
        return self._result(status, reason, detail=detail, opportunity=opportunity, **fields)

    # ------------------------------------------------------------------
    # Input helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _read_quote(market: Mapping[str, Any]) -> tuple[float, float, float] | None:
        """Return (bid, ask, point) or None when no quote was supplied; reject partial/invalid quotes."""
        if all(market.get(key) is None for key in ("bid", "ask", "point")):
            return None
        try:
            bid = _number(market.get("bid"), "bid", "INVALID_QUOTE")
            ask = _number(market.get("ask"), "ask", "INVALID_QUOTE")
            point = _number(market.get("point"), "point", "INVALID_QUOTE")
        except _Invalid:
            raise _Invalid("INVALID_QUOTE", "bid, ask and point must all be supplied as numbers") from None
        if not all(map(math.isfinite, (bid, ask, point))) or bid <= 0 or ask < bid or point <= 0:
            raise _Invalid("INVALID_QUOTE", "quote must satisfy 0 < bid <= ask and point > 0")
        return bid, ask, point

    @staticmethod
    def _signal_age_bars(opportunity: Mapping[str, Any], market: Mapping[str, Any]) -> float | None:
        """Completed bars between the signal bar and the latest bar, or None if not determinable."""
        signal_time = opportunity.get("signal_bar_time")
        if signal_time is None:
            return None
        bars = market.get("bars")
        latest = market.get("latest_bar_time")
        try:
            if latest is None and isinstance(bars, (list, tuple)) and bars and isinstance(bars[-1], Mapping):
                latest = bars[-1].get("time")
            if latest is None:
                return None
            signal, latest_time = parse_timestamp(signal_time), parse_timestamp(latest)
        except InvalidMarketData as exc:
            raise _Invalid("SIGNAL_TIME_INVALID", exc.detail) from None
        interval = _TIMEFRAME_SECONDS.get(str(market.get("timeframe", "M15")).upper())
        if interval is None:
            interval = market.get("bar_interval_seconds")
        if not isinstance(interval, (int, float)) or isinstance(interval, bool) or interval <= 0:
            return None
        return (latest_time - signal) / interval
