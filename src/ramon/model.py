from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from .core import Forecast


class ChronosForecaster:
    """Load a real Chronos-2 checkpoint; model failures never produce orders."""

    def __init__(self, model_id: str = "autogluon/chronos-2-small", device: str = "cpu") -> None:
        import numpy as np
        from chronos import Chronos2Pipeline

        self._np = np
        self.model_id = model_id
        self.pipeline = Chronos2Pipeline.from_pretrained(model_id, device_map=device)

    def forecast(self, closes: Sequence[float], horizon: int) -> Forecast:
        values = self._np.asarray(closes, dtype=self._np.float32)
        quantiles, _ = self.pipeline.predict_quantiles(
            [values], prediction_length=horizon, quantile_levels=[0.1, 0.5, 0.9]
        )
        low, median, high = quantiles[0][0, -1, :].tolist()
        return Forecast(float(low), float(median), float(high))


def model_name(value: str) -> str:
    """Allow only an explicit local checkpoint or a named Chronos-2 model."""
    if value in {"autogluon/chronos-2-small", "amazon/chronos-2"}:
        return value
    path = Path(value).expanduser().resolve()
    if not path.is_dir() or not (path / "config.json").exists():
        raise ValueError("model must be a supported Chronos-2 ID or local checkpoint")
    return str(path)
