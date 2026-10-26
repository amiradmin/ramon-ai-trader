"""Stage 1: detect explainable trend-pullback opportunities.

Candle-based evaluation is preferred when completed OHLC candles are supplied.
The legacy trend/setup_score contract remains supported for callers that have
not yet been migrated. This module never submits orders or sizes positions.
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Mapping, Sequence


class OpportunityDetector:
    """Evaluate market snapshots for candidate trend-following pullbacks.

    market["candles"] may contain chronological mappings with open, high, low
    and close numeric fields and an optional timestamp. Candles should be
    completed candles; the most recent candle is used as the decision candle.
    """

    def __init__(
        self,
        minimum_score: float = 0.65,
        *,
        use_statistical_forecasts: bool = False,
        forecast_limit: int = 200,
        arima_order: tuple[int, int, int] = (1, 1, 1),
        sarima_order: tuple[int, int, int] = (1, 1, 1),
        sarima_seasonal_order: tuple[int, int, int, int] = (1, 0, 1, 5),
        minimum_candles: int = 30,
        fast_ema_period: int = 8,
        slow_ema_period: int = 21,
        atr_period: int = 14,
    ) -> None:
        if not 0.0 <= minimum_score <= 1.0:
            raise ValueError("minimum_score must be between 0 and 1")
        if forecast_limit < 40:
            raise ValueError("forecast_limit must be at least 40 candles")
        if minimum_candles < 25:
            raise ValueError("minimum_candles must be at least 25")
        if not 2 <= fast_ema_period < slow_ema_period:
            raise ValueError("EMA periods must satisfy 2 <= fast < slow")
        if minimum_candles < slow_ema_period + 5:
            raise ValueError("minimum_candles must be at least slow_ema_period + 5")
        if atr_period < 2:
            raise ValueError("atr_period must be at least 2")
        if minimum_candles < atr_period + 1:
            raise ValueError("minimum_candles must be at least atr_period + 1")
        self.minimum_score = minimum_score
        self.use_statistical_forecasts = use_statistical_forecasts
        self.forecast_limit = forecast_limit
        self.arima_order = arima_order
        self.sarima_order = sarima_order
        self.sarima_seasonal_order = sarima_seasonal_order
        self.minimum_candles = minimum_candles
        self.fast_ema_period = fast_ema_period
        self.slow_ema_period = slow_ema_period
        self.atr_period = atr_period
        # Per-process guard; signal_key is also returned for durable downstream
        # deduplication across worker restarts.
        self._last_signal_by_market: dict[tuple[str, str], str] = {}

    @staticmethod
    def _ema(values: Sequence[float], period: int) -> float:
        alpha = 2.0 / (period + 1.0)
        result = float(values[0])
        for value in values[1:]:
            result = alpha * float(value) + (1.0 - alpha) * result
        return result

    @staticmethod
    def _timestamp_order_key(value: Any) -> tuple[str, Any]:
        """Normalize common timestamp forms without lexically sorting numbers.

        Non-date labels (used by some test/replay adapters) retain lexical order.
        Mixing timestamp representations is rejected by the caller.
        """
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if not math.isfinite(float(value)):
                raise ValueError("timestamp must be finite")
            return ("numeric", float(value))
        text = str(value).strip()
        try:
            return ("numeric", float(text))
        except ValueError:
            pass
        try:
            normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
            return ("datetime", datetime.fromisoformat(normalized).timestamp())
        except ValueError:
            return ("label", text)

    @staticmethod
    def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
        return max(low, min(high, value))

    @staticmethod
    def _rejected(reason: str, **details: Any) -> dict[str, Any]:
        return {
            "approved": False,
            "side": "HOLD",
            "score": 0.0,
            "score_points": 0.0,
            "reason": reason,
            **details,
        }

    def _read_candles(self, market: Mapping[str, Any]) -> tuple[list[dict[str, Any]] | None, str | None]:
        raw = market.get("candles")
        if not isinstance(raw, (list, tuple)):
            return None, "CANDLES_REQUIRED"
        if len(raw) < self.minimum_candles:
            return None, "INSUFFICIENT_CANDLE_HISTORY"

        candles: list[dict[str, Any]] = []
        previous_timestamp_key: tuple[str, Any] | None = None
        for index, raw_candle in enumerate(raw):
            if not isinstance(raw_candle, Mapping):
                return None, "INVALID_CANDLE"
            try:
                candle = {
                    key: float(raw_candle[key])
                    for key in ("open", "high", "low", "close")
                }
            except (KeyError, TypeError, ValueError, OverflowError):
                return None, "MISSING_OR_INVALID_OHLC"
            if not all(math.isfinite(value) and value > 0 for value in candle.values()):
                return None, "NON_FINITE_OR_NON_POSITIVE_OHLC"
            if candle["high"] < max(candle["open"], candle["close"], candle["low"]):
                return None, "INCONSISTENT_CANDLE_RANGE"
            if candle["low"] > min(candle["open"], candle["close"], candle["high"]):
                return None, "INCONSISTENT_CANDLE_RANGE"

            timestamp = raw_candle.get("timestamp")
            if timestamp is not None:
                try:
                    timestamp_key = self._timestamp_order_key(timestamp)
                except (TypeError, ValueError, OverflowError):
                    return None, "INVALID_CANDLE_TIMESTAMP"
                if previous_timestamp_key is not None:
                    if timestamp_key[0] != previous_timestamp_key[0]:
                        return None, "INCONSISTENT_CANDLE_TIMESTAMP_FORMAT"
                    if timestamp_key[1] <= previous_timestamp_key[1]:
                        return None, "CANDLE_TIMESTAMPS_NOT_STRICTLY_INCREASING"
                previous_timestamp_key = timestamp_key
                timestamp = str(timestamp)
            if (raw_candle.get("is_closed") is False or raw_candle.get("closed") is False) and index == len(raw) - 1:
                return None, "LATEST_CANDLE_NOT_CLOSED"
            candles.append({**candle, "timestamp": timestamp})

        return candles, None

    def _evaluate_candles(self, market: Mapping[str, Any]) -> dict[str, Any]:
        candles, error = self._read_candles(market)
        if error or candles is None:
            return self._rejected(error or "INVALID_CANDLE_HISTORY")

        opens = [c["open"] for c in candles]
        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]
        closes = [c["close"] for c in candles]
        history = closes[-max(self.slow_ema_period * 3, 30):]
        fast = self._ema(history, self.fast_ema_period)
        slow = self._ema(history, self.slow_ema_period)

        true_ranges = []
        for index in range(1, len(candles)):
            true_ranges.append(max(
                highs[index] - lows[index],
                abs(highs[index] - closes[index - 1]),
                abs(lows[index] - closes[index - 1]),
            ))
        atr = sum(true_ranges[-self.atr_period:]) / min(self.atr_period, len(true_ranges))
        if not math.isfinite(atr) or atr <= 0:
            return self._rejected("INVALID_VOLATILITY_ESTIMATE")

        # Use only completed historical closes when estimating trend slope.
        prior_slow = self._ema(history[:-5], self.slow_ema_period)
        slow_slope_atr = (slow - prior_slow) / atr
        ema_gap_atr = (fast - slow) / atr

        if fast > slow and closes[-1] > slow and slow_slope_atr >= 0.04:
            regime, side = "BULLISH", "BUY"
        elif fast < slow and closes[-1] < slow and slow_slope_atr <= -0.04:
            regime, side = "BEARISH", "SELL"
        elif abs(ema_gap_atr) <= 0.15 and abs(slow_slope_atr) <= 0.12:
            regime, side = "RANGING", "HOLD"
        else:
            regime, side = "UNCERTAIN", "HOLD"

        indicators = {
            "fast_ema": round(fast, 8),
            "slow_ema": round(slow, 8),
            "atr": round(atr, 8),
            "slow_slope_atr": round(slow_slope_atr, 4),
            "ema_gap_atr": round(ema_gap_atr, 4),
        }
        if side == "HOLD":
            return self._rejected("NO_TREND_PULLBACK_SETUP", market_regime=regime, indicators=indicators)

        trend_score = self._clamp(0.55 + abs(ema_gap_atr) * 0.12 + abs(slow_slope_atr) * 0.08)
        recent_lows, recent_highs = lows[-4:-1], highs[-4:-1]
        distance_to_fast_atr = abs(closes[-1] - fast) / atr
        if side == "BUY":
            pullback_seen = min(recent_lows) <= fast + 0.25 * atr
            resumed = closes[-1] > fast and closes[-1] >= opens[-1]
        else:
            pullback_seen = max(recent_highs) >= fast - 0.25 * atr
            resumed = closes[-1] < fast and closes[-1] <= opens[-1]

        pullback_score = self._clamp(1.0 - max(0.0, distance_to_fast_atr - 0.15) / 1.5)
        if not pullback_seen:
            pullback_score *= 0.25
        if not resumed:
            pullback_score *= 0.35

        candle_range = max(highs[-1] - lows[-1], 1e-12)
        close_location = (closes[-1] - lows[-1]) / candle_range
        aligned_candle = closes[-1] > opens[-1] if side == "BUY" else closes[-1] < opens[-1]
        favorable_close = close_location if side == "BUY" else 1.0 - close_location
        momentum_score = self._clamp(0.55 * float(aligned_candle) + 0.45 * favorable_close)

        atr_fraction = atr / closes[-1]
        if not 0.00002 <= atr_fraction <= 0.03:
            volatility_score = 0.0
        elif 0.0002 <= atr_fraction <= 0.008:
            volatility_score = 1.0
        else:
            volatility_score = 0.65

        weights = {"trend": 25.0, "pullback": 25.0, "momentum": 20.0, "volatility": 15.0}
        components = {
            "trend": trend_score,
            "pullback": pullback_score,
            "momentum": momentum_score,
            "volatility": volatility_score,
        }
        weighted_points = sum(weights[key] * components[key] for key in weights)
        score = weighted_points / sum(weights.values())
        common = {
            "market_regime": regime,
            "side_candidate": side,
            "score": round(score, 4),
            "score_points": round(weighted_points, 2),
            "score_components": {key: round(value, 4) for key, value in components.items()},
        }
        if not pullback_seen:
            return self._rejected("PULLBACK_NOT_CONFIRMED", **common)
        if not resumed:
            return self._rejected("PULLBACK_CONTINUATION_NOT_CONFIRMED", **common)
        if volatility_score == 0.0:
            return self._rejected("VOLATILITY_OUT_OF_RANGE", **common)
        if score < self.minimum_score:
            return self._rejected("SETUP_SCORE_TOO_LOW", **common)

        last_timestamp = candles[-1].get("timestamp")
        symbol = str(market.get("symbol", "")).strip()
        timeframe = str(market.get("timeframe", "M15")).upper()
        candle_identity = last_timestamp or "|".join(
            f"{candles[-1][key]:.10g}" for key in ("open", "high", "low", "close")
        )
        signal_key = f"{symbol}|{timeframe}|{candle_identity}"
        return {
            "approved": True,
            "side": side,
            "score": round(score, 4),
            "score_points": round(weighted_points, 2),
            "reason": "OPPORTUNITY_FOUND",
            "market_regime": regime,
            "signal_key": signal_key,
            "score_components": {key: round(value, 4) for key, value in components.items()},
            "indicators": {
                **indicators,
                "atr_fraction": round(atr_fraction, 8),
                "distance_to_fast_atr": round(distance_to_fast_atr, 4),
            },
            "evidence": {
                "pullback_seen": pullback_seen,
                "continuation_candle": resumed,
                "aligned_momentum_candle": aligned_candle,
                "latest_candle_timestamp": last_timestamp,
            },
        }

    @staticmethod
    def _forecast_direction(result: Any) -> str:
        last_close, forecast_close = float(result.last_close), float(result.forecast_close)
        if not math.isfinite(last_close) or not math.isfinite(forecast_close):
            raise ValueError("forecast prices must be finite")
        if forecast_close > last_close:
            return "BUY"
        if forecast_close < last_close:
            return "SELL"
        return "HOLD"

    def _evaluate_forecasts(self, market: Mapping[str, Any], side: str) -> dict[str, Any]:
        """Run opt-in research forecasts only after a candidate passes scoring."""
        symbol = str(market.get("symbol", "")).strip()
        timeframe = str(market.get("timeframe", "M15")).upper()
        source_value = market.get("source")
        source = str(source_value) if source_value else None
        if not symbol:
            return {"approved": False, "reason": "FORECAST_SYMBOL_REQUIRED"}

        try:
            # Lazy imports keep statsmodels and rolling model fitting out of the
            # default path. These evaluations can be expensive, so they are opt-in.
            from .forecasting.arima import forecast_market_bars
            from .forecasting.sarima import forecast_market_bars_sarima

            arima = forecast_market_bars(
                symbol=symbol, timeframe=timeframe, source=source,
                limit=self.forecast_limit, order=self.arima_order,
            )
            sarima = forecast_market_bars_sarima(
                symbol=symbol, timeframe=timeframe, source=source,
                limit=self.forecast_limit, order=self.sarima_order,
                seasonal_order=self.sarima_seasonal_order,
            )
            arima_side, sarima_side = self._forecast_direction(arima), self._forecast_direction(sarima)
        except (ValueError, RuntimeError, ArithmeticError) as exc:
            return {"approved": False, "reason": "FORECAST_UNAVAILABLE", "detail": str(exc)}
        except Exception as exc:
            return {"approved": False, "reason": "FORECAST_ERROR", "detail": f"{type(exc).__name__}: {exc}"}

        agreed = arima_side == sarima_side == side and side in {"BUY", "SELL"}
        return {
            "approved": agreed,
            "reason": "FORECASTS_AGREE" if agreed else "FORECAST_DIRECTION_MISMATCH",
            "side": side if agreed else "HOLD",
            "arima": arima.as_dict(),
            "sarima": sarima.as_dict(),
            "arima_direction": arima_side,
            "sarima_direction": sarima_side,
        }

    def evaluate(self, market: Mapping[str, Any]) -> dict[str, Any]:
        """Return a candidate opportunity without entering or sizing a trade."""
        if not isinstance(market, Mapping):
            return self._rejected("INVALID_MARKET_SNAPSHOT")

        if "candles" in market:
            result = self._evaluate_candles(market)
        else:
            # Backward-compatible adapter for existing pipeline callers. New
            # candle-based callers should use the OHLC strategy above.
            side = str(market.get("trend", "")).upper()
            try:
                score = float(market.get("setup_score", 0.0))
            except (TypeError, ValueError, OverflowError):
                score = 0.0
            if side not in {"BUY", "SELL"}:
                return self._rejected("NO_VALID_DIRECTION", score=score)
            if not math.isfinite(score) or not 0.0 <= score <= 1.0:
                return self._rejected("INVALID_SETUP_SCORE", score=score)
            if score < self.minimum_score:
                return self._rejected("SETUP_SCORE_TOO_LOW", score=score)
            result = {
                "approved": True,
                "side": side,
                "score": score,
                "score_points": round(score * 100.0, 2),
                "reason": "OPPORTUNITY_FOUND",
                "market_regime": "LEGACY_SIGNAL",
                "signal_key": "|".join((
                    str(market.get("symbol", "")).strip(),
                    str(market.get("timeframe", "M15")).upper(),
                    str(market.get("timestamp", "legacy")),
                )),
            }

        if not result.get("approved"):
            return result

        identity = (
            str(market.get("symbol", "")).strip(),
            str(market.get("timeframe", "M15")).upper(),
        )
        if self._last_signal_by_market.get(identity) == result.get("signal_key"):
            return self._rejected(
                "DUPLICATE_SIGNAL",
                signal_key=result.get("signal_key"),
                market_regime=result.get("market_regime"),
            )

        if self.use_statistical_forecasts or bool(market.get("use_statistical_forecasts", False)):
            forecast_result = self._evaluate_forecasts(market, str(result["side"]))
            result["forecasting"] = forecast_result
            if not forecast_result.get("approved", False):
                result.update(approved=False, side="HOLD", reason=forecast_result.get("reason", "FORECAST_REJECTED"))
            else:
                # Forecast is a gate, not a score boost or an independent signal.
                result["reason"] = "OPPORTUNITY_FOUND_WITH_FORECAST_CONFIRMATION"

        if result.get("approved"):
            self._last_signal_by_market[identity] = str(result.get("signal_key", ""))
        return result
