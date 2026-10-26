"""Stage 1: identify opportunities using market signals and optional ARIMA/SARIMA evidence.

Statistical forecasts are opt-in because the current forecasting functions perform
rolling backtests and can be computationally expensive. A forecast never enables
order execution; it is only one input to opportunity evaluation.
"""
from typing import Any, Mapping


class OpportunityDetector:
    def __init__(
        self,
        minimum_score: float = 0.65,
        *,
        use_statistical_forecasts: bool = False,
        forecast_limit: int = 200,
        arima_order: tuple[int, int, int] = (1, 1, 1),
        sarima_order: tuple[int, int, int] = (1, 1, 1),
        sarima_seasonal_order: tuple[int, int, int, int] = (1, 0, 1, 5),
    ) -> None:
        if not 0.0 <= minimum_score <= 1.0:
            raise ValueError("minimum_score must be between 0 and 1")
        if forecast_limit < 40:
            raise ValueError("forecast_limit must be at least 40 candles")
        self.minimum_score = minimum_score
        self.use_statistical_forecasts = use_statistical_forecasts
        self.forecast_limit = forecast_limit
        self.arima_order = arima_order
        self.sarima_order = sarima_order
        self.sarima_seasonal_order = sarima_seasonal_order

    @staticmethod
    def _forecast_direction(result: Any) -> str:
        last_close = float(result.last_close)
        forecast_close = float(result.forecast_close)
        if forecast_close > last_close:
            return "BUY"
        if forecast_close < last_close:
            return "SELL"
        return "HOLD"

    def _evaluate_forecasts(self, market: Mapping[str, Any], side: str) -> dict[str, Any]:
        """Run opt-in statistical forecasts and require directional agreement.

        This currently invokes rolling-evaluation functions, so callers should
        enable it only in a worker/research path, not a latency-sensitive EA
        heartbeat or order-execution request.
        """
        symbol = str(market.get("symbol", "")).strip()
        timeframe = str(market.get("timeframe", "M15")).upper()
        source_value = market.get("source")
        source = str(source_value) if source_value else None
        if not symbol:
            return {"approved": False, "reason": "FORECAST_SYMBOL_REQUIRED"}

        try:
            # Lazy imports keep statsmodels and model fitting out of the default path.
            from .forecasting.arima import forecast_market_bars
            from .forecasting.sarima import forecast_market_bars_sarima

            arima = forecast_market_bars(
                symbol=symbol,
                timeframe=timeframe,
                source=source,
                limit=self.forecast_limit,
                order=self.arima_order,
            )
            sarima = forecast_market_bars_sarima(
                symbol=symbol,
                timeframe=timeframe,
                source=source,
                limit=self.forecast_limit,
                order=self.sarima_order,
                seasonal_order=self.sarima_seasonal_order,
            )
        except (ValueError, RuntimeError, ArithmeticError) as exc:
            return {
                "approved": False,
                "reason": "FORECAST_UNAVAILABLE",
                "detail": str(exc),
            }
        except Exception as exc:
            # Forecast failures must not silently approve a trade.
            return {
                "approved": False,
                "reason": "FORECAST_ERROR",
                "detail": f"{type(exc).__name__}: {exc}",
            }

        arima_side = self._forecast_direction(arima)
        sarima_side = self._forecast_direction(sarima)
        agreed = arima_side == sarima_side and arima_side == side and side in {"BUY", "SELL"}
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
        side = str(market.get("trend", "")).upper()
        try:
            score = float(market.get("setup_score", 0.0))
        except (TypeError, ValueError):
            score = 0.0

        if side not in {"BUY", "SELL"}:
            return {
                "approved": False,
                "side": "HOLD",
                "score": score,
                "reason": "NO_VALID_DIRECTION",
            }
        if not 0.0 <= score <= 1.0:
            return {
                "approved": False,
                "side": "HOLD",
                "score": score,
                "reason": "INVALID_SETUP_SCORE",
            }
        if score < self.minimum_score:
            return {
                "approved": False,
                "side": "HOLD",
                "score": score,
                "reason": "SETUP_SCORE_TOO_LOW",
            }

        result: dict[str, Any] = {
            "approved": True,
            "side": side,
            "score": score,
            "reason": "OPPORTUNITY_FOUND",
        }
        if self.use_statistical_forecasts or bool(market.get("use_statistical_forecasts", False)):
            forecast_result = self._evaluate_forecasts(market, side)
            result["forecasting"] = forecast_result
            if not forecast_result.get("approved", False):
                result.update(
                    approved=False,
                    side="HOLD",
                    reason=forecast_result.get("reason", "FORECAST_REJECTED"),
                )
            else:
                result["reason"] = "OPPORTUNITY_FOUND_WITH_FORECAST_CONFIRMATION"
        return result
