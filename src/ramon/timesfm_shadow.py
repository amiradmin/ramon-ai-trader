"""Optional TimesFM 3 display-only forecast.

This module is deliberately isolated from Ramon execution. TimesFM 3 pretrained
weights currently carry a non-commercial/non-production license, so the adapter
is disabled by default and its output can never alter BUY/SELL/WAIT.
"""
from __future__ import annotations

import math
import os
from typing import Any

from .core import Decision, Market


DEFAULT_TIMESFM3_CHECKPOINT = "google/timesfm-3.0-pytorch"


class TimesFM3Experimental:
    """Best-effort secondary forecast for observation and later evaluation only."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        checkpoint: str = DEFAULT_TIMESFM3_CHECKPOINT,
        device: str = "cpu",
        forecaster: Any | None = None,
    ) -> None:
        self.enabled = bool(enabled)
        self.checkpoint = checkpoint
        self.device = device
        self.forecaster = forecaster
        self.error = ""
        self._np = None

        if not self.enabled:
            self.error = "disabled"
            return
        if self.forecaster is not None:
            return
        try:
            import numpy as np
            from timesfm3 import TimesFM3Forecaster

            self._np = np
            self.forecaster = TimesFM3Forecaster.from_pretrained(checkpoint)
        except Exception as exc:
            self.forecaster = None
            self.error = f"{type(exc).__name__}: {exc}"

    @classmethod
    def from_env(cls) -> "TimesFM3Experimental":
        enabled = os.getenv("RAMON_TIMESFM3_EXPERIMENTAL_ENABLED", "0").strip().lower() in {
            "1", "true", "yes", "on"
        }
        return cls(
            enabled=enabled,
            checkpoint=os.getenv(
                "RAMON_TIMESFM3_CHECKPOINT", DEFAULT_TIMESFM3_CHECKPOINT
            ).strip() or DEFAULT_TIMESFM3_CHECKPOINT,
            device=os.getenv("RAMON_TIMESFM3_DEVICE", "cpu").strip() or "cpu",
        )

    @property
    def ready(self) -> bool:
        return self.enabled and self.forecaster is not None

    def status(self) -> dict[str, object]:
        return {
            "timesfm3_experimental_enabled": int(self.enabled),
            "timesfm3_experimental_ready": int(self.ready),
            "timesfm3_experimental_checkpoint": self.checkpoint,
            "timesfm3_experimental_error": self.error,
            "timesfm3_experimental_effect": "NONE",
        }

    @staticmethod
    def _flatten(values: Any) -> list[float]:
        if hasattr(values, "detach"):
            values = values.detach().cpu().numpy()
        if hasattr(values, "tolist"):
            values = values.tolist()
        while isinstance(values, list) and len(values) == 1 and isinstance(values[0], list):
            values = values[0]
        if not isinstance(values, list):
            raise ValueError("invalid TimesFM forecast")
        return [float(v) for v in values]

    def assess(self, market: Market, decision: Decision, horizon: int = 4) -> dict[str, object]:
        payload: dict[str, object] = {
            **self.status(),
            "timesfm3_experimental_direction": "UNAVAILABLE",
            "timesfm3_experimental_low": -1.0,
            "timesfm3_experimental_median": -1.0,
            "timesfm3_experimental_high": -1.0,
            "timesfm3_experimental_move_atr": -1.0,
            "timesfm3_experimental_agrees_chronos": 0,
        }
        if not self.ready:
            return payload

        try:
            closes = [float(bar.close) for bar in market.bars[-256:]]
            context: Any = closes
            if self._np is not None:
                context = self._np.asarray(closes, dtype=self._np.float32)
            output = self.forecaster.predict(
                context=context,
                horizon=horizon,
                return_quantiles=True,
            )
            forecast = self._flatten(output.forecast)
            if len(forecast) < horizon:
                raise ValueError("short TimesFM forecast")
            median = float(forecast[horizon - 1])
            low = high = median

            quantiles = getattr(output, "quantiles", None)
            if quantiles is not None:
                if hasattr(quantiles, "detach"):
                    quantiles = quantiles.detach().cpu().numpy()
                if hasattr(quantiles, "tolist"):
                    quantiles = quantiles.tolist()
                while (
                    isinstance(quantiles, list)
                    and len(quantiles) == 1
                    and isinstance(quantiles[0], list)
                    and quantiles[0]
                    and isinstance(quantiles[0][0], list)
                ):
                    quantiles = quantiles[0]
                if (
                    isinstance(quantiles, list)
                    and len(quantiles) >= horizon
                    and isinstance(quantiles[horizon - 1], list)
                    and len(quantiles[horizon - 1]) >= 9
                ):
                    row = quantiles[horizon - 1]
                    low, median, high = float(row[0]), float(row[4]), float(row[8])

            if not all(math.isfinite(v) and v > 0 for v in (low, median, high)):
                raise ValueError("invalid TimesFM values")
            if not low <= median <= high:
                raise ValueError("invalid TimesFM quantile ordering")

            last = closes[-1]
            direction = "BUY" if median > last else "SELL" if median < last else "NONE"
            atr = max(float(decision.atr), float(market.point))
            move_atr = abs(median - last) / atr
            chronos_direction = (
                "BUY" if decision.buy_edge >= decision.sell_edge else "SELL"
            )
            payload.update(
                {
                    "timesfm3_experimental_ready": 1,
                    "timesfm3_experimental_error": "",
                    "timesfm3_experimental_direction": direction,
                    "timesfm3_experimental_low": low,
                    "timesfm3_experimental_median": median,
                    "timesfm3_experimental_high": high,
                    "timesfm3_experimental_move_atr": move_atr,
                    "timesfm3_experimental_agrees_chronos": int(
                        direction in {"BUY", "SELL"} and direction == chronos_direction
                    ),
                }
            )
        except Exception as exc:
            payload["timesfm3_experimental_ready"] = 0
            payload["timesfm3_experimental_error"] = f"{type(exc).__name__}: {exc}"
        return payload
