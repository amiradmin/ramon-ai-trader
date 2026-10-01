"""Offline-only Chronos-2 past-covariate adapter; never used by the live service."""
from __future__ import annotations

from math import isfinite
from typing import Sequence

from .core import Bar, Forecast, Market


def past_covariates(bars: Sequence[Bar]) -> dict[str, list[float]]:
    if not bars:
        raise ValueError("empty covariate context")
    values = {name: [] for name in ("range", "body", "return", "atr14")}
    ranges = []
    for i, bar in enumerate(bars):
        if not all(isfinite(x) and x > 0 for x in (bar.open, bar.high, bar.low, bar.close)):
            raise ValueError("invalid OHLC covariates")
        previous = bars[i - 1].close if i else bar.open
        ranges.append(max(bar.high - bar.low, abs(bar.high - previous), abs(bar.low - previous)))
        values["range"].append(bar.high - bar.low)
        values["body"].append(bar.close - bar.open)
        values["return"].append(bar.close / previous - 1)
        values["atr14"].append(sum(ranges[-14:]) / len(ranges[-14:]))
    return values


class CovariateForecaster:
    def __init__(self, baseline):
        self.baseline = baseline

    def for_market(self, market: Market):
        return _BoundForecaster(self.baseline, market.bars)


class _BoundForecaster:
    def __init__(self, baseline, bars):
        self.baseline, self.bars = baseline, bars

    def forecast(self, closes, horizon):
        bars = self.bars[-len(closes):]
        if horizon < 1 or tuple(closes) != tuple(bar.close for bar in bars):
            raise ValueError("covariate/target alignment mismatch")
        np = self.baseline._np
        task = {"target": np.asarray(closes, dtype=np.float32),
                "past_covariates": {name: np.asarray(values, dtype=np.float32)
                                    for name, values in past_covariates(bars).items()}}
        quantiles, _ = self.baseline.pipeline.predict_quantiles(
            [task], prediction_length=horizon, quantile_levels=[0.1, 0.5, 0.9])
        rows = quantiles[0][0].tolist()
        if len(rows) != horizon or any(len(row) != 3 for row in rows):
            raise ValueError("invalid covariate forecast output")
        if any(not all(isfinite(float(x)) and float(x) > 0 for x in row)
               or not row[0] <= row[1] <= row[2] for row in rows):
            raise ValueError("invalid covariate quantiles")
        low, median, high = rows[-1]
        return Forecast(float(low), float(median), float(high), tuple(float(row[1]) for row in rows))
