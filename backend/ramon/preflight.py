"""Download/load the actual Chronos-2 model and run one local inference."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict

from .model import ChronosForecaster, model_name


def main() -> None:
    parser = argparse.ArgumentParser(description="Check real Ramon model inference, without MT5/orders")
    parser.add_argument("--model", default="autogluon/chronos-2-small")
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()

    model = ChronosForecaster(model_name(args.model), args.device)
    closes = [4350.0 + 0.025 * i + 0.3 * math.sin(i / 12) for i in range(256)]
    forecast = model.forecast(closes, horizon=4)
    if not (
        all(math.isfinite(value) and value > 0 for value in asdict(forecast).values())
        and forecast.low <= forecast.median <= forecast.high
    ):
        raise SystemExit("FAIL: Chronos-2 returned invalid quantiles")
    print(json.dumps({"ready": True, "model": model.model_id, "forecast": asdict(forecast)}))


if __name__ == "__main__":
    main()
