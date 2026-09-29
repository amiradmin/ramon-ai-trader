"""Optional, offline-only TimesFM 2.5 adapter for the historical comparison.

Never imported by the live server or EA. Its weights and dependencies are kept
out of the production image until a chronological evaluation justifies them.
"""

from __future__ import annotations

from math import isfinite
from typing import Sequence

from .core import Forecast


CHECKPOINT = "google/timesfm-2.5-200m-pytorch"


class TimesFMShadow:
    def __init__(self, checkpoint: str = CHECKPOINT, *, context: int = 256,
                 backend=None) -> None:
        if context < 16:
            raise ValueError("context must be >=16")
        self.checkpoint = checkpoint
        self.context = context
        if backend is None:
            try:
                import timesfm
            except ImportError as exc:
                raise RuntimeError("Install the research extra: uv sync --extra timesfm-shadow") from exc
            backend = timesfm.TimesFM_2p5_200M_torch.from_pretrained(checkpoint)
            backend.compile(timesfm.ForecastConfig(
                max_context=context, max_horizon=4,
                use_continuous_quantile_head=True, fix_quantile_crossing=True,
            ))
        self.backend = backend

    def forecast(self, closes: Sequence[float], horizon: int) -> Forecast:
        if horizon != 4 or len(closes) < 16 or not all(isfinite(v) and v > 0 for v in closes):
            raise ValueError("need >=16 positive finite closes and the Ramon 4-bar horizon")
        import numpy as np
        point, quantiles = self.backend.forecast(
            horizon=horizon, inputs=[np.asarray(closes[-self.context:], dtype=np.float32)]
        )
        path = tuple(float(v) for v in point[0])
        low, median, high = (
            float(quantiles[0, -1, index]) for index in (1, 5, 9)
        )
        if not (len(path) == horizon and all(isfinite(v) and v > 0 for v in path)
                and 0 < low <= median <= high):
            raise ValueError("TimesFM produced invalid forecast quantiles")
        return Forecast(low, median, high, path)
