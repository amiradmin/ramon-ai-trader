from __future__ import annotations

import argparse
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
from threading import Lock
from concurrent.futures import ThreadPoolExecutor
import time
from uuid import uuid4

from .core import Forecast, Market, Settings, evaluate
from .ensemble import EnsembleCoordinator, dominant_direction
from .shadow_roles import ShadowCoordinator
from .history import persist_decision_sample, persist_market, persist_trade_outcome, persist_replay_input
from .model import ChronosForecaster, model_name
from .news import DEFAULT_FOREX_FACTORY_JSON, ForexFactoryNewsProvider
from .target_learning import build_target_structure
from .target_outcomes import backfill_target_outcomes
from .timesfm_shadow import TimesFM3Shadow
from .moment_shadow import MomentAnomalyShadow
from .finbert_shadow import FinBertNewsShadow
from .range_shadow import observe as observe_range_shadow
from .range_strategy import live_candidate
from .market_state import assess_market, apply_market_policy


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
    range_live_enabled = os.getenv("RAMON_RANGE_LIVE_ENABLED", "0") == "1"
    ensemble_dir = os.getenv("RAMON_ENSEMBLE_DIR", "/checkpoints/ensemble").strip()
    # Preview is the default; enabling the live ensemble requires an explicit mode.
    role_mode = os.getenv("RAMON_ROLE_MODE", "shadow").strip().lower()
    if role_mode not in {"shadow", "live"}:
        raise ValueError("RAMON_ROLE_MODE must be shadow or live")
    coordinator = ShadowCoordinator if role_mode == "shadow" else EnsembleCoordinator
    ensemble = coordinator(ensemble_dir, model.model_id)
    news_enabled = os.getenv("RAMON_NEWS_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
    timesfm3_shadow = TimesFM3Shadow.from_env()
    moment_shadow = MomentAnomalyShadow.from_env(lazy=True)
    finbert_shadow = FinBertNewsShadow.from_env(lazy=True)
    moment_live_enabled = os.getenv("RAMON_MOMENT_LIVE_ENABLED", "1").strip().lower() in {"1","true","yes","on"}
    moment_live_threshold = float(os.getenv("RAMON_MOMENT_LIVE_THRESHOLD", "2.0"))
    finbert_live_enabled = os.getenv("RAMON_FINBERT_LIVE_ENABLED", "1").strip().lower() in {"1","true","yes","on"}
    finbert_live_threshold = float(os.getenv("RAMON_FINBERT_LIVE_THRESHOLD", "0.35"))
    shadow_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ramon-shadow")
    # Warm heavyweight external models in the background so /health becomes
    # available immediately after Chronos is ready.
    if moment_shadow.enabled:
        shadow_executor.submit(moment_shadow.load)
    if finbert_shadow.enabled:
        shadow_executor.submit(finbert_shadow.load)
    moment_future = None
    finbert_future = None
    latest_moment_payload = {
        **moment_shadow.status(),
        "moment_anomaly_score": -1.0,
        "moment_anomaly_ratio": -1.0,
        "moment_anomaly_label": "WARMING" if moment_shadow.enabled else "UNAVAILABLE",
        "moment_anomaly_bar_time": 0,
    }
    latest_finbert_payload = {
        **finbert_shadow.status(),
        "finbert_sentiment_label": "WARMING" if finbert_shadow.enabled else "UNAVAILABLE",
        "finbert_positive": -1.0,
        "finbert_negative": -1.0,
        "finbert_neutral": -1.0,
        "finbert_directional_score": 0.0,
    }
    last_shadow_bar = 0
    last_finbert_event_key = ""
    news_provider = ForexFactoryNewsProvider(
        enabled=news_enabled,
        url=os.getenv("RAMON_NEWS_URL", DEFAULT_FOREX_FACTORY_JSON).strip() or DEFAULT_FOREX_FACTORY_JSON,
        refresh_seconds=int(os.getenv("RAMON_NEWS_REFRESH_SECONDS", "300")),
        timeout_seconds=float(os.getenv("RAMON_NEWS_TIMEOUT_SECONDS", "2.0")),
        max_stale_seconds=int(os.getenv("RAMON_NEWS_MAX_STALE_SECONDS", "1800")),
    )

    def role_handler_name(role: str) -> str:
        role_model = getattr(ensemble, role, None)
        class_name = type(role_model).__name__ if role_model is not None else "BinaryLogisticModel"
        return f"Ramon/{class_name}"

    def model_handlers() -> dict[str, str]:
        return {
            "forecast_model_handler": model.model_id.split("/")[-1],
            "forecast_shadow_model_handler": (
                timesfm3_shadow.checkpoint.split("/")[-1]
                if timesfm3_shadow.enabled else "OFF"
            ),
            "anomaly_model_handler": (
                moment_shadow.checkpoint.split("/")[-1]
                if moment_shadow.enabled else "OFF"
            ),
            "news_sentiment_model_handler": (
                finbert_shadow.checkpoint.split("/")[-1]
                if finbert_shadow.enabled else "OFF"
            ),
            "regime_model_handler": role_handler_name("regime"),
            "entry_model_handler": role_handler_name("entry"),
            "news_model_handler": role_handler_name("news"),
            "meta_model_handler": role_handler_name("meta"),
            "risk_model_handler": role_handler_name("risk"),
            "news_source_handler": "ForexFactoryNewsProvider",
            "market_state_handler": "market-state-v1",
            "target_model_handler": "Ramon/TargetStructure",
        }
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
                    "market_state_policy_enabled": settings.market_state_policy_enabled,
                    "replay_inputs_enabled": bool(history_db),
                    "model": model.model_id,
                    "forecast_context": "completed_m15_cached",
                    "live_quote_decisions": True,
                    "history_enabled": bool(history_db),
                    "allow_weak_intrabar_entries": settings.allow_weak_intrabar_entries,
                    "minimum_strength": settings.minimum_strength,
                    "weak_entry_policy": "forecast_intrabar_ai_trend_agreement",
                    "require_direction_confirmation": settings.require_direction_confirmation,
                    "maximum_entry_extension_atr": settings.maximum_entry_extension_atr,
                    "range_shadow_mode": "OBSERVE_ONLY" if history_db else "DISABLED",
                    "range_main_enabled": range_live_enabled,
                    "history_last_error": str(history_status["last_error"]),
                    "history_last_persisted_bar": int(history_status["last_persisted_bar"]),
                    **ensemble.status(),
                    **timesfm3_shadow.status(),
                    **moment_shadow.status(),
                    **finbert_shadow.status(),
                    **news_provider.status(),
                    **model_handlers(),
                    "moment_live_enabled": int(moment_live_enabled),
                    "moment_live_threshold": moment_live_threshold,
                    "finbert_live_enabled": int(finbert_live_enabled),
                    "finbert_live_threshold": finbert_live_threshold,
                },
            )

        def do_POST(self) -> None:
            nonlocal moment_future, finbert_future, latest_moment_payload
            nonlocal latest_finbert_payload, last_shadow_bar, last_finbert_event_key
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
                    try:
                        backfill_target_outcomes(history_db, symbol=str(payload.get("symbol", "XAUUSD_l")))
                    except Exception as exc:
                        print(
                            f"Ramon target-outcome backfill warning: {type(exc).__name__}: {exc}",
                            flush=True,
                        )
                    self.reply(200, {"saved": True})
                    return
                market = Market.from_dict(payload)
                quote_time = int(payload["quote_time"]) if "quote_time" in payload else None
                market.validate_quote_context(quote_time)

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
                            try:
                                backfill_target_outcomes(history_db, symbol=market.symbol)
                            except Exception as exc:
                                print(
                                    f"Ramon target-outcome backfill warning: {type(exc).__name__}: {exc}",
                                    flush=True,
                                )

                news_snapshot = news_provider.snapshot()
                with guard:
                    result = evaluate(market, cached_model, settings)
                    audit_forecast = cached_model._forecast if result.forecast_median>0 else None
                    ensemble_payload, feature_snapshot = ensemble.assess(
                        market, result, news_snapshot.features
                    )
                    timesfm3_payload = timesfm3_shadow.assess(
                        market, result, settings.horizon
                    )

                # Heavy shadow inference must never delay the trading response.
                # Collect completed results, then schedule at most one new job per input.
                if moment_future is not None and moment_future.done():
                    try:
                        latest_moment_payload = moment_future.result()
                    except Exception as exc:
                        latest_moment_payload = {
                            **moment_shadow.status(),
                            "moment_shadow_ready": 0,
                            "moment_shadow_error": f"{type(exc).__name__}: {exc}",
                            "moment_anomaly_score": -1.0,
                            "moment_anomaly_ratio": -1.0,
                            "moment_anomaly_label": "ERROR",
                        }
                    moment_future = None
                current_bar = market.bars[-1].time
                if moment_shadow.ready and moment_future is None and current_bar != last_shadow_bar:
                    moment_future = shadow_executor.submit(moment_shadow.assess, market)
                    last_shadow_bar = current_bar

                if finbert_future is not None and finbert_future.done():
                    try:
                        latest_finbert_payload = finbert_future.result()
                    except Exception as exc:
                        latest_finbert_payload = {
                            **finbert_shadow.status(),
                            "finbert_shadow_ready": 0,
                            "finbert_shadow_error": f"{type(exc).__name__}: {exc}",
                            "finbert_sentiment_label": "ERROR",
                            "finbert_positive": -1.0,
                            "finbert_negative": -1.0,
                            "finbert_neutral": -1.0,
                            "finbert_directional_score": 0.0,
                        }
                    finbert_future = None
                event_key = f"{news_snapshot.event_time}:{news_snapshot.event_title}"
                if (
                    finbert_shadow.ready
                    and finbert_future is None
                    and news_snapshot.event_title != "NONE"
                    and event_key != last_finbert_event_key
                ):
                    finbert_future = shadow_executor.submit(
                        finbert_shadow.assess,
                        title=news_snapshot.event_title,
                        country=news_snapshot.event_country,
                        impact=news_snapshot.event_impact,
                    )
                    last_finbert_event_key = event_key

                response = result.to_dict()
                response.update(ensemble_payload)
                response.update(timesfm3_payload)
                response.update(latest_moment_payload)
                response.update(latest_finbert_payload)
                if history_db:
                    try:
                        response.update(observe_range_shadow(history_db, market, quote_time))
                    except Exception as exc:
                        response["range_shadow_status"] = "ERROR"
                        response["range_shadow_error"] = str(exc)

                # The terminal owns the loss-streak gate. Uploaded history can lag
                # fresh closes or omit intervening outcomes; it is not authoritative.
                response["loss_streak_cooldown_source"] = "mt5_history"

                response.update(news_snapshot.payload())
                response["news_model_ready"] = int(ensemble.news_ready)
                response.update(model_handlers())

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
                range_setup = live_candidate(
                    market, response, enabled=range_live_enabled,
                    capable=payload.get("range_execution_ready") is True,
                    quote_time=quote_time, max_spread_points=settings.max_spread_points,
                )
                response["range_execution"] = int(range_setup is not None)
                if range_setup:
                    response.update({
                        "decision": range_setup["direction"],
                        "reason": "range_reversal_" + range_setup["direction"].lower(),
                        "stop_distance": range_setup["stop_distance"],
                        "target_distance": range_setup["target_distance"],
                        "range_stop_price": range_setup["stop"],
                        "range_target_price": range_setup["target"],
                        "range_low": range_setup["low"], "range_high": range_setup["high"],
                        "target_structure_ready": 0, "target_method": "range_midpoint",
                        "target_direction": range_setup["direction"],
                        "target_tp1": 0.0, "target_tp2": 0.0, "target_tp3": 0.0,
                        "legacy_target_price": range_setup["target"],
                    })
                    target_payload = {"ready": 0, "method": "range_midpoint",
                                      "direction": range_setup["direction"],
                                      "legacy_target": range_setup["target"]}
                market_assessment = assess_market(market, max_spread_points=settings.max_spread_points)
                if settings.market_state_policy_enabled:
                    apply_market_policy(response, market_assessment)
                else:
                    response.update(market_state=market_assessment["state"], market_state_route="OBSERVE",
                                    market_state_policy=market_assessment["version"])

                # Active external-model vetoes. They may only turn a proposed
                # entry into WAIT; they can never create or reverse a trade.
                current_bar = int(market.bars[-1].time)
                moment_fresh = (
                    int(response.get("moment_shadow_ready", 0)) == 1
                    and int(response.get("moment_anomaly_bar_time", 0)) == current_bar
                )
                moment_ratio = float(response.get("moment_anomaly_ratio", -1.0))
                moment_veto = (
                    moment_live_enabled
                    and moment_fresh
                    and moment_ratio >= moment_live_threshold
                )
                finbert_ready = int(response.get("finbert_shadow_ready", 0)) == 1
                finbert_score = abs(float(response.get("finbert_directional_score", 0.0)))
                finbert_veto = (
                    finbert_live_enabled
                    and finbert_ready
                    and finbert_score >= finbert_live_threshold
                    and str(response.get("finbert_sentiment_label", "UNAVAILABLE")) != "NEUTRAL"
                )
                response["moment_live_active"] = int(moment_live_enabled)
                response["moment_live_fresh"] = int(moment_fresh)
                response["moment_live_veto"] = int(moment_veto)
                response["moment_live_threshold"] = moment_live_threshold
                response["finbert_live_active"] = int(finbert_live_enabled)
                response["finbert_live_veto"] = int(finbert_veto)
                response["finbert_live_threshold"] = finbert_live_threshold

                if str(response.get("decision", "")) in {"BUY", "SELL"}:
                    if moment_veto:
                        response["decision"] = "WAIT"
                        response["reason"] = "moment_anomaly_veto"
                        response["range_execution"] = 0
                    elif finbert_veto:
                        response["decision"] = "WAIT"
                        response["reason"] = "finbert_sentiment_veto"
                        response["range_execution"] = 0

                if not response["range_execution"]:
                    range_setup = None
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
                            direction=range_setup["direction"] if range_setup else dominant_direction(result),
                            base_decision=result.decision,
                            regime_features=feature_snapshot["regime"],
                            entry_features=feature_snapshot["entry"],
                            news_features=feature_snapshot["news"],
                            meta_base_features=feature_snapshot["meta_base"],
                            sample_key=sample_key,
                            chronos_model="range-reversal-v1" if range_setup else model.model_id,
                            quote_time=quote_time,
                            stop_distance=float(response["stop_distance"]),
                            target_distance=float(response["target_distance"]),
                            final_decision=str(response["decision"]),
                            bundle_id=ensemble.bundle_id,
                            target_structure=target_payload,
                            model_metadata={
                                "chronos_revision": getattr(model, "revision", None),
                                "ensemble_mode": ensemble.status()["ensemble_mode"],
                                "ensemble_active": response.get("ensemble_active", 0),
                                "role_manifest": ensemble.manifest,
                                "execution_strategy": "range-reversal-v1" if range_setup else "chronos",
                                "range_setup": range_setup,
                                "market_assessment": market_assessment,
                                "decision_audit": {
                                    "schema_version": 2,
                                    "base": result.to_dict(),
                                    "final": {**ensemble_payload, "decision": response["decision"],
                                              "reason": response["reason"],
                                              "range_execution": response["range_execution"],
                                              "market_state": response["market_state"],
                                              "market_state_route": response["market_state_route"]},
                                    "settings": asdict(settings),
                                    "handlers": model_handlers(),
                                    "external_models": {
                                        "moment": dict(latest_moment_payload),
                                        "finbert": dict(latest_finbert_payload),
                                    },
                                    "shadow_forecasts": {
                                        "timesfm3": timesfm3_payload,
                                        "direction_quality": {
                                            "buy_success_probability": response.get(
                                                "shadow_buy_success_probability", -1.0
                                            ),
                                            "sell_success_probability": response.get(
                                                "shadow_sell_success_probability", -1.0
                                            ),
                                            "full_sl_probability": response.get(
                                                "shadow_full_sl_probability", -1.0
                                            ),
                                        },
                                    },
                                },
                            },
                        )
                        response["sample_saved"] = int(saved)
                        response["replay_input_saved"] = 0
                        if saved and audit_forecast is not None:
                            try:
                                response["replay_input_saved"] = int(persist_replay_input(
                                    history_db, sample_key=sample_key, market=market, quote_time=quote_time,
                                    forecast=audit_forecast, settings=settings, response={**response, "replay_input_saved": 1}))
                            except Exception as exc:
                                print(f"Ramon replay-input warning: {type(exc).__name__}: {exc}", flush=True)
                    except Exception as exc:
                        print(
                            f"Ramon decision-sample persistence warning: {type(exc).__name__}: {exc}",
                            flush=True,
                        )

                if range_setup and not response["sample_saved"]:
                    response["decision"] = "WAIT"
                    response["reason"] = "range_sample_not_saved"
                    response["range_execution"] = 0
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
