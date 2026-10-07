from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
import os
from threading import Lock, Thread
import time

import numpy as np

from .core import Market
from .model import ChronosForecaster


class MultivariateChronos2:
    """Display-only Chronos-2 forecaster using OHLC targets plus time/price covariates."""

    def __init__(self, model_id: str = "amazon/chronos-2", device: str = "cpu") -> None:
        self.base = ChronosForecaster(model_id, device)
        self.model_id = model_id
        self.pipeline = self.base.pipeline
        self.lock = Lock()

    @staticmethod
    def _time_covariates(times: list[int], future: int) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
        day = 86400.0
        week = 7.0 * day
        past_t = np.asarray(times, dtype=np.float64)
        step = 900
        future_t = np.asarray([times[-1] + step * (i + 1) for i in range(future)], dtype=np.float64)

        def encode(values: np.ndarray) -> dict[str, np.ndarray]:
            return {
                "tod_sin": np.sin(2.0 * math.pi * (values % day) / day).astype(np.float32),
                "tod_cos": np.cos(2.0 * math.pi * (values % day) / day).astype(np.float32),
                "tow_sin": np.sin(2.0 * math.pi * (values % week) / week).astype(np.float32),
                "tow_cos": np.cos(2.0 * math.pi * (values % week) / week).astype(np.float32),
            }

        return encode(past_t), encode(future_t)

    def forecast(self, market: Market, horizon: int) -> dict[str, object]:
        bars = list(market.bars)
        target = np.asarray(
            [
                [float(b.open) for b in bars],
                [float(b.high) for b in bars],
                [float(b.low) for b in bars],
                [float(b.close) for b in bars],
            ],
            dtype=np.float32,
        )
        past_time, future_time = self._time_covariates([int(b.time) for b in bars], horizon)
        ranges = np.asarray([float(b.high - b.low) for b in bars], dtype=np.float32)
        bodies = np.asarray([float(b.close - b.open) for b in bars], dtype=np.float32)
        past_covariates = {
            **past_time,
            "range": ranges,
            "body": bodies,
        }

        # Future range/body are unknown, so only genuinely known calendar covariates
        # are supplied into the future. Chronos-2 accepts future keys as a subset of past.
        inputs = [{
            "target": target,
            "past_covariates": past_covariates,
            "future_covariates": future_time,
        }]
        with self.lock:
            quantiles, _ = self.pipeline.predict_quantiles(
                inputs,
                prediction_length=horizon,
                quantile_levels=[0.1, 0.5, 0.9],
            )

        rows = quantiles[0]
        if hasattr(rows, "detach"):
            rows = rows.detach().cpu().numpy()
        rows = np.asarray(rows)
        if rows.ndim != 3 or rows.shape[0] < 4 or rows.shape[1] != horizon or rows.shape[2] < 3:
            raise ValueError(f"unexpected Chronos-2 multivariate output shape {rows.shape}")

        close_rows = rows[3]
        low = float(close_rows[-1, 0])
        median = float(close_rows[-1, 1])
        high = float(close_rows[-1, 2])
        path = [float(x) for x in close_rows[:, 1].tolist()]
        if not all(math.isfinite(x) and x > 0 for x in [low, median, high, *path]):
            raise ValueError("invalid Chronos-2 multivariate forecast")
        return {
            "low": low,
            "median": median,
            "high": high,
            "path": path,
        }


class ShadowModelState:
    def __init__(self, model_id: str, device: str) -> None:
        self.model_id = model_id
        self.device = device
        self.model: MultivariateChronos2 | None = None
        self.status = "loading"
        self.error = ""
        self.started_utc = int(time.time())
        self.ready_utc = 0
        self._thread = Thread(target=self._load, name="chronos-shadow-loader", daemon=True)
        self._thread.start()

    def _load(self) -> None:
        try:
            self.model = MultivariateChronos2(self.model_id, self.device)
            self.ready_utc = int(time.time())
            self.status = "ready"
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            self.status = "error"

    @property
    def ready(self) -> bool:
        return self.status == "ready" and self.model is not None


def serve(host: str, port: int, state: ShadowModelState) -> None:
    started = int(time.time())

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: object) -> None:
            return

        def reply(self, code: int, payload: dict[str, object]) -> None:
            body = json.dumps(payload, allow_nan=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path.split("?", 1)[0] != "/health":
                self.send_error(404)
                return
            payload = {
                "ready": state.ready,
                "status": state.status,
                "model": state.model_id,
                "mode": "shadow_multivariate_covariate",
                "effect": "NONE",
                "uptime_seconds": int(time.time()) - started,
                "loading_seconds": (
                    max(0, (state.ready_utc or int(time.time())) - state.started_utc)
                ),
                "error": state.error,
            }
            self.reply(200 if state.ready else 503, payload)

        def do_POST(self) -> None:
            if self.path.split("?", 1)[0] != "/forecast-only":
                self.send_error(404)
                return
            try:
                if not state.ready or state.model is None:
                    self.reply(503, {
                        "ready": False,
                        "status": state.status,
                        "model": state.model_id,
                        "error": state.error,
                        "advisory_only": True,
                    })
                    return
                model = state.model
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length))
                market = Market.from_dict(payload)
                quote_time = int(payload["quote_time"]) if "quote_time" in payload else None
                if quote_time is not None:
                    last_bar_time = int(market.bars[-1].time)
                    if not (last_bar_time <= quote_time <= last_bar_time + 930):
                        market.validate_quote_context(quote_time)
                horizon = int(payload.get("forecast_horizon_bars", 4))
                if horizon < 1 or horizon > 16:
                    raise ValueError("forecast_horizon_bars must be 1..16")
                result = model.forecast(market, horizon)
                midpoint = (float(market.bid) + float(market.ask)) / 2.0
                path = list(result["path"])
                move = path[-1] - midpoint
                deadband = max(float(market.point) * 5.0, abs(midpoint) * 1e-7)
                direction = "UP" if move > deadband else "DOWN" if move < -deadband else "FLAT"
                width = max(float(result["high"]) - float(result["low"]), float(market.point))

                def conf(price: float, idx: int) -> float:
                    displacement = abs(price - midpoint)
                    score = displacement / (displacement + 0.5 * width)
                    return max(0.35, min(0.95, 0.50 + 0.45 * score - 0.04 * max(0, idx - 1)))

                cs = [conf(v, i) for i, v in enumerate(path, 1)]
                self.reply(200, {
                    "model": model.model_id,
                    "mode": "shadow_multivariate_covariate",
                    "direction": direction,
                    "current_mid": midpoint,
                    "forecast_low": result["low"],
                    "forecast_median": result["median"],
                    "forecast_high": result["high"],
                    "forecast_median_path": path,
                    "forecast_step_1": path[0],
                    "forecast_step_2": path[1] if horizon >= 2 else path[0],
                    "forecast_step_3": path[2] if horizon >= 3 else path[-1],
                    "forecast_step_4": path[3] if horizon >= 4 else path[-1],
                    "forecast_step_confidence_1": cs[0],
                    "forecast_step_confidence_2": cs[1] if horizon >= 2 else cs[0],
                    "forecast_step_confidence_3": cs[2] if horizon >= 3 else cs[-1],
                    "forecast_step_confidence_4": cs[3] if horizon >= 4 else cs[-1],
                    "forecast_horizon_bars": horizon,
                    "forecast_horizon_minutes": horizon * 15,
                    "captured_utc": int(time.time()),
                    "advisory_only": True,
                    "ramon_decision_independent": True,
                    "inputs": {
                        "targets": ["open", "high", "low", "close"],
                        "past_covariates": ["range", "body", "tod_sin", "tod_cos", "tow_sin", "tow_cos"],
                        "future_covariates": ["tod_sin", "tod_cos", "tow_sin", "tow_cos"],
                    },
                })
            except Exception as exc:
                self.reply(400, {"error": f"{type(exc).__name__}: {exc}"})

    HTTPServer((host, port), Handler).serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Shadow multivariate/covariate Chronos-2 service")
    parser.add_argument("--model", default=os.getenv("CHRONOS_SHADOW_MODEL", "amazon/chronos-2"))
    parser.add_argument("--device", default=os.getenv("CHRONOS_SHADOW_DEVICE", "cpu"))
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8016)
    args = parser.parse_args()
    state = ShadowModelState(args.model, args.device)
    serve(args.host, args.port, state)


if __name__ == "__main__":
    main()
