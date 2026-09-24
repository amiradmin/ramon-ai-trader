from __future__ import annotations

import argparse
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
from threading import Lock
import time
from uuid import uuid4

from .core import Forecast, Market, Settings, evaluate
from .ensemble import EnsembleCoordinator, dominant_direction
from .history import persist_decision_sample, persist_market, persist_trade_outcome
from .model import ChronosForecaster, model_name
from .news import DEFAULT_FOREX_FACTORY_JSON, ForexFactoryNewsProvider
from .target_learning import build_target_structure


def persist_market_safely(db: str, market: Market) -> str:
    """Best-effort learning telemetry; never make a trading decision fail."""
    try:
        persist_market(db, market)
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"
    return ""


class CachedForecaster:
    """Reuse the Chronos forecast while the completed M15 context is unchanged."""

    def __init__(self, model: ChronosForecaster) -> None:
        self.model = model
        self.model_id = model.model_id
        self._key: tuple[int, tuple[float, ...]] | None = None
        self._forecast: Forecast | None = None

    def forecast(self, closes: list[float], horizon: int) -> Forecast:
        key = (horizon, tuple(float(value) for value in closes))
        if self._key != key or self._forecast is None:
            self._forecast = self.model.forecast(closes, horizon)
            self._key = key
        return self._forecast


def serve(host: str, port: int, model: ChronosForecaster, settings: Settings) -> None:
    """Serve Chronos plus learned regime/entry/news/meta roles."""
    guard = Lock()
    cached_model = CachedForecaster(model)
    history_db = os.getenv("RAMON_HISTORY_DB", "").strip()
    ensemble_dir = os.getenv("RAMON_ENSEMBLE_DIR", "/checkpoints/ensemble").strip()
    ensemble = EnsembleCoordinator(ensemble_dir, model.model_id)
    news_enabled = os.getenv("RAMON_NEWS_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
    news_provider = ForexFactoryNewsProvider(
        enabled=news_enabled,
        url=os.getenv("RAMON_NEWS_URL", DEFAULT_FOREX_FACTORY_JSON).strip() or DEFAULT_FOREX_FACTORY_JSON,
        refresh_seconds=int(os.getenv("RAMON_NEWS_REFRESH_SECONDS", "300")),
        timeout_seconds=float(os.getenv("RAMON_NEWS_TIMEOUT_SECONDS", "2.0")),
        max_stale_seconds=int(os.getenv("RAMON_NEWS_MAX_STALE_SECONDS", "1800")),
    )
    last_persisted_bar: dict[str, int] = {}
    history_status: dict[str, object] = {
        "last_error": "",
        "last_persisted_bar": 0,
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/health":
                self.send_error(404)
                return
            self.reply(
                200,
                {
                    "ready": True,
                    "model": model.model_id,
                    "forecast_context": "completed_m15_cached",
                    "live_quote_decisions": True,
                    "history_enabled": bool(history_db),
                    "history_last_error": str(history_status["last_error"]),
                    "history_last_persisted_bar": int(history_status["last_persisted_bar"]),
                    **ensemble.status(),
                    **news_provider.status(),
                },
            )

        def do_POST(self) -> None:
            if self.path not in {"/decision", "/trades"}:
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 2 or length > 250_000:
                    raise ValueError("invalid body size")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("request must be a JSON object")
                if self.path == "/trades":
                    if not history_db:
                        raise ValueError("history persistence is disabled")
                    persist_trade_outcome(history_db, payload, int(time.time()))
                    self.reply(200, {"saved": True})
                    return
                market = Market.from_dict(payload)
                quote_time = int(payload["quote_time"]) if "quote_time" in payload else None
                if quote_time is not None and not market.bars[-1].time + 900 <= quote_time <= market.bars[-1].time + 1830:
                    raise ValueError("quote time is inconsistent with completed M15 bars")

                if history_db:
                    key = f"{market.symbol}:{market.timeframe}"
                    newest = market.bars[-1].time
                    if last_persisted_bar.get(key) != newest:
                        history_error = persist_market_safely(history_db, market)
                        history_status["last_error"] = history_error
                        if history_error:
                            print(
                                f"Ramon history persistence warning: {history_error}",
                                flush=True,
                            )
                        else:
                            last_persisted_bar[key] = newest
                            history_status["last_persisted_bar"] = newest

                news_snapshot = news_provider.snapshot()
                with guard:
                    result = evaluate(market, cached_model, settings)
                    ensemble_payload, feature_snapshot = ensemble.assess(
                        market, result, news_snapshot.features
                    )

                response = result.to_dict()
                response.update(ensemble_payload)
                response.update(news_snapshot.payload())
                response["news_model_ready"] = int(ensemble.news_ready)

                target_direction = (
                    str(response["decision"])
                    if str(response.get("decision", "")) in {"BUY", "SELL"}
                    else dominant_direction(result)
                )
                target_structure = build_target_structure(
                    market,
                    direction=target_direction,
                    atr=result.atr,
                    target_distance=result.target_distance,
                )
                target_payload = target_structure.to_dict()
                response["target_learning_active"] = 1
                response["target_structure_ready"] = target_structure.ready
                response["target_method"] = target_structure.method
                response["target_direction"] = target_structure.direction
                response["target_impulse_start"] = target_structure.impulse_start
                response["target_impulse_end"] = target_structure.impulse_end
                response["target_impulse_range"] = target_structure.impulse_range
                response["target_impulse_atr"] = target_structure.impulse_atr
                response["target_tp1"] = target_structure.tp1
                response["target_tp2"] = target_structure.tp2
                response["target_tp3"] = target_structure.tp3
                response["legacy_target_price"] = target_structure.legacy_target
                sample_key = uuid4().hex[:16]
                response["sample_key"] = sample_key
                response["sample_saved"] = 0

                if history_db:
                    try:
                        saved = persist_decision_sample(
                            history_db,
                            captured=int(time.time()),
                            market=market,
                            signal_bar_time=result.signal_bar_time,
                            atr=result.atr,
                            direction=dominant_direction(result),
                            base_decision=result.decision,
                            regime_features=feature_snapshot["regime"],
                            entry_features=feature_snapshot["entry"],
                            news_features=feature_snapshot["news"],
                            meta_base_features=feature_snapshot["meta_base"],
                            sample_key=sample_key,
                            chronos_model=model.model_id,
                            quote_time=quote_time,
                            stop_distance=result.stop_distance,
                            target_distance=result.target_distance,
                            final_decision=str(response["decision"]),
                            bundle_id=ensemble.bundle_id,
                            target_structure=target_payload,
                            model_metadata={
                                "chronos_revision": getattr(model, "revision", None),
                                "ensemble_mode": ensemble.status()["ensemble_mode"],
                                "ensemble_active": response.get("ensemble_active", 0),
                                "role_manifest": ensemble.manifest,
                                "decision_audit": {
                                    "schema_version": 2,
                                    "base": result.to_dict(),
                                    "final": ensemble_payload,
                                    "settings": asdict(settings),
                                },
                            },
                        )
                        response["sample_saved"] = int(saved)
                    except Exception as exc:
                        print(
                            f"Ramon decision-sample persistence warning: {type(exc).__name__}: {exc}",
                            flush=True,
                        )

                self.reply(200, response)
            except (ValueError, TypeError, KeyError, OverflowError, json.JSONDecodeError) as exc:
                self.reply(400, {"error": str(exc)})
            except Exception as exc:
                print(f"Ramon decision failure: {type(exc).__name__}: {exc}", flush=True)
                self.reply(503, {"error": "model_unavailable"})

        def reply(self, status: int, data: dict[str, object]) -> None:
            body = json.dumps(data, separators=(",", ":"), allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    if host not in {"127.0.0.1", "localhost", "0.0.0.0"}:
        raise ValueError("unsupported bind address")
    HTTPServer((host, port), Handler).serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Chronos-2 local decision service")
    parser.add_argument("--model", default=os.getenv("CHRONOS_MODEL", "autogluon/chronos-2-small"))
    parser.add_argument("--device", default=os.getenv("CHRONOS_DEVICE", "cpu"))
    parser.add_argument("--port", type=int, default=8012)
    parser.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1", "0.0.0.0"))
    args = parser.parse_args()

    requested_model = args.model
    model_file = os.getenv("CHRONOS_MODEL_FILE", "").strip()
    if model_file:
        path = Path(model_file)
        if path.is_file():
            candidate = path.read_text(encoding="utf-8").strip()
            if candidate:
                requested_model = candidate

    checkpoint = model_name(requested_model)
    model = ChronosForecaster(checkpoint, args.device)
    serve(args.host, args.port, model, Settings())


if __name__ == "__main__":
    main()
