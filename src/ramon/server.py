from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit
import json
import os
import sqlite3
from pathlib import Path
from threading import Lock
from concurrent.futures import ThreadPoolExecutor
import time
from uuid import uuid4

from .core import Forecast, Market, Settings, evaluate, independent_market_direction, independent_entry_timing
from .ensemble import EnsembleCoordinator, dominant_direction
from .shadow_roles import ShadowCoordinator, DirectionCoordinator
from .history import persist_decision_sample, persist_market, persist_trade_outcome, persist_replay_input
from .model import ChronosForecaster, model_name
from .news import DEFAULT_FOREX_FACTORY_JSON, ForexFactoryNewsProvider
from .target_learning import build_target_structure
from .target_outcomes import backfill_target_outcomes
from .timesfm_shadow import TimesFM3Shadow
from .moment_shadow import MomentAnomalyShadow
from .finbert_shadow import FinBertNewsShadow
from .range_shadow import observe as observe_range_shadow
from .range_strategy import candidate as range_candidate, live_candidate
from .market_state import POLICY_VERSION, assess_market, apply_market_policy
from .reversal_strategy import apply_reversal
from .trade_selector import apply_live_selector
from .ai_decision_engine import apply_ai_decision_engine



ANALYTIC_OVERRIDE_STAGES = {"timing", "extension", "edge", "strength", "market_direction", "entry_timing", "base", "decision", "range", "news", "account", "limits", "risk", "order", "position"}


def _ensure_human_opinion_table(db: str) -> None:
    if not db:
        return
    with sqlite3.connect(db) as con:
        con.execute(
            """CREATE TABLE IF NOT EXISTS human_market_opinions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_utc INTEGER NOT NULL,
                reviewer TEXT NOT NULL,
                symbol TEXT NOT NULL,
                sample_key TEXT NOT NULL,
                signal_bar_time INTEGER NOT NULL,
                opinion TEXT NOT NULL,
                confidence INTEGER NOT NULL,
                note TEXT NOT NULL,
                snapshot_json TEXT NOT NULL
            )"""
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_human_market_opinions_time "
            "ON human_market_opinions(symbol, created_utc DESC)"
        )
        con.commit()


def _persist_human_opinion(db: str, payload: dict[str, object]) -> None:
    if not db:
        raise ValueError("history persistence is disabled")
    reviewer = str(payload.get("reviewer", "Sahar")).strip()[:64] or "Sahar"
    symbol = str(payload.get("symbol", "XAUUSD_l")).strip()
    sample_key = str(payload.get("sample_key", "")).strip()
    signal_bar_time = int(payload.get("signal_bar_time", 0))
    opinion = str(payload.get("opinion", "")).upper()
    confidence = int(payload.get("confidence", 0))
    note = str(payload.get("note", "")).strip()[:2000]
    snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
    if not symbol.startswith("XAUUSD") or not sample_key or signal_bar_time <= 0:
        raise ValueError("invalid opinion context")
    if opinion not in {"BUY", "SELL", "WAIT"}:
        raise ValueError("opinion must be BUY, SELL or WAIT")
    if confidence not in {1, 2, 3, 4, 5}:
        raise ValueError("confidence must be 1..5")
    _ensure_human_opinion_table(db)
    with sqlite3.connect(db) as con:
        con.execute(
            """INSERT INTO human_market_opinions
               (created_utc,reviewer,symbol,sample_key,signal_bar_time,opinion,confidence,note,snapshot_json)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                int(time.time()), reviewer, symbol, sample_key, signal_bar_time,
                opinion, confidence, note,
                json.dumps(snapshot, ensure_ascii=False, allow_nan=False),
            ),
        )
        con.commit()


def _recent_human_opinions(db: str, symbol: str = "XAUUSD_l", limit: int = 12) -> list[dict[str, object]]:
    if not db:
        return []
    _ensure_human_opinion_table(db)
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """SELECT created_utc,reviewer,symbol,sample_key,signal_bar_time,opinion,confidence,note
               FROM human_market_opinions WHERE symbol=?
               ORDER BY id DESC LIMIT ?""",
            (symbol, max(1, min(int(limit), 50))),
        ).fetchall()
    return [dict(row) for row in rows]


def _ensure_override_table(db: str) -> None:
    if not db:
        return
    with sqlite3.connect(db) as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS manual_gate_overrides (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_utc INTEGER NOT NULL,
                signal_bar_time INTEGER NOT NULL,
                sample_key TEXT NOT NULL,
                stage TEXT NOT NULL,
                action TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )
        con.commit()


def _persist_manual_override(db: str, payload: dict[str, object]) -> None:
    if not db:
        return
    _ensure_override_table(db)
    with sqlite3.connect(db) as con:
        con.execute(
            """INSERT INTO manual_gate_overrides
               (created_utc, signal_bar_time, sample_key, stage, action, payload_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                int(time.time()),
                int(payload["signal_bar_time"]),
                str(payload["sample_key"]),
                str(payload["stage"]),
                str(payload.get("action", "FORCE_PASS")),
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
            ),
        )
        con.commit()

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
    latest_market: Market | None = None
    latest_market_received_utc = 0
    latest_display_forecast: dict[str, object] | None = None
    history_db = os.getenv("RAMON_HISTORY_DB", "").strip()
    range_live_enabled = os.getenv("RAMON_RANGE_LIVE_ENABLED", "0") == "1"
    reversal_live_enabled = os.getenv("RAMON_REVERSAL_LIVE_ENABLED", "0") == "1"
    ensemble_dir = os.getenv("RAMON_ENSEMBLE_DIR", "/checkpoints/ensemble").strip()
    # Direction-live uses the independently trained BUY/SELL quality roles as
    # an operational source while retaining the validated bundle path for full live mode.
    role_mode = os.getenv("RAMON_ROLE_MODE", "direction_live").strip().lower()
    if role_mode not in {"shadow", "direction_live", "live"}:
        raise ValueError("RAMON_ROLE_MODE must be shadow, direction_live or live")
    coordinator = (
        ShadowCoordinator if role_mode == "shadow"
        else DirectionCoordinator if role_mode == "direction_live"
        else EnsembleCoordinator
    )
    ensemble = coordinator(ensemble_dir, model.model_id)
    news_enabled = os.getenv("RAMON_NEWS_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
    timesfm3_shadow = TimesFM3Shadow.from_env()
    moment_shadow = MomentAnomalyShadow.from_env(lazy=True)
    finbert_shadow = FinBertNewsShadow.from_env(lazy=True)
    moment_live_enabled = os.getenv("RAMON_MOMENT_LIVE_ENABLED", "1").strip().lower() in {"1","true","yes","on"}
    moment_live_threshold = float(os.getenv("RAMON_MOMENT_LIVE_THRESHOLD", "2.0"))
    moment_hard_veto_threshold = float(os.getenv("RAMON_MOMENT_HARD_VETO_THRESHOLD", "5.0"))
    finbert_live_enabled = os.getenv("RAMON_FINBERT_LIVE_ENABLED", "1").strip().lower() in {"1","true","yes","on"}
    finbert_live_threshold = float(os.getenv("RAMON_FINBERT_LIVE_THRESHOLD", "0.35"))
    selector_live_enabled = os.getenv("RAMON_SELECTOR_LIVE_ENABLED", "1").strip().lower() in {"1","true","yes","on"}
    selector_minimum_quality = float(os.getenv("RAMON_SELECTOR_MIN_QUALITY", "0.55"))
    selector_maximum_full_sl = float(os.getenv("RAMON_SELECTOR_MAX_FULL_SL", "0.50"))
    selector_minimum_margin = float(os.getenv("RAMON_SELECTOR_MIN_MARGIN", "0.05"))
    ai_engine_v2_enabled = os.getenv("RAMON_AI_ENGINE_V2_ENABLED", "1").strip().lower() in {"1","true","yes","on"}
    ai_engine_v2_minimum_score = float(os.getenv("RAMON_AI_ENGINE_V2_MIN_SCORE", "0.56"))
    ai_engine_v2_minimum_margin = float(os.getenv("RAMON_AI_ENGINE_V2_MIN_MARGIN", "0.03"))
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
    manual_overrides_by_bar: dict[int, dict[str, dict[str, object]]] = {}
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
            "market_state_handler": POLICY_VERSION,
            "target_model_handler": "Ramon/TargetStructure",
            "decision_engine_handler": "Ramon/AIEngineV2",
        }
    last_persisted_bar: dict[str, int] = {}
    history_status: dict[str, object] = {
        "last_error": "",
        "last_persisted_bar": 0,
    }
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            route = self.path.split("?", 1)[0]
            if route == "/forecast-display":
                # Read-only snapshot produced exclusively by /forecast-only (Bridge).
                result = latest_display_forecast
                age = int(time.time()) - int(result.get("captured_utc", 0)) if result else 10**9
                if not result or age < 0 or age > 90:
                    self.reply(200, {"ready": False, "reason": "no_fresh_bridge_forecast", "advisory_only": True})
                else:
                    self.reply(200, {**result, "ready": True, "age_seconds": age})
                return
            if route == "/human-opinions":
                params = parse_qs(urlsplit(self.path).query)
                symbol = str(params.get("symbol", ["XAUUSD_l"])[0])
                limit = int(params.get("limit", ["12"])[0])
                self.reply(200, {"opinions": _recent_human_opinions(history_db, symbol, limit)})
                return
            if route != "/health":
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
                    "position_exit_advice": True,
                    "opportunity_entry_advice": True,
                    "history_enabled": bool(history_db),
                    "allow_weak_intrabar_entries": settings.allow_weak_intrabar_entries,
                    "minimum_strength": settings.minimum_strength,
                    "weak_entry_policy": "forecast_intrabar_ai_trend_agreement",
                    "require_direction_confirmation": settings.require_direction_confirmation,
                    "cent_independent_direction_gate": True,
                    "cent_independent_direction_gate_role": "fallback_only" if ai_engine_v2_enabled else "primary",
                    "analytical_decision_mode": "AI_ENGINE_V2_PRIMARY" if ai_engine_v2_enabled else "LEGACY_RULE_GATES",
                    "cent_direction_vote_threshold": 2,
                    "cent_entry_timing_min_move_atr": settings.trend_min_micro_move_atr,
                    "cent_entry_timing_min_rebound_atr": settings.intrabar_min_rebound_atr,
                    "trend_min_consistency": settings.trend_min_consistency,
                    "trend_min_path_atr": settings.trend_min_path_atr,
                    "trend_min_micro_move_atr": settings.trend_min_micro_move_atr,
                    "maximum_entry_extension_atr": settings.maximum_entry_extension_atr,
                    "range_shadow_mode": "OBSERVE_ONLY" if history_db else "DISABLED",
                    "range_main_enabled": range_live_enabled,
                    "reversal_live_enabled": reversal_live_enabled,
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
                    "moment_hard_veto_threshold": moment_hard_veto_threshold,
                    "finbert_live_enabled": int(finbert_live_enabled),
                    "finbert_live_threshold": finbert_live_threshold,
                    "selector_live_enabled": int(selector_live_enabled),
                    "selector_minimum_quality": selector_minimum_quality,
                    "selector_maximum_full_sl_probability": selector_maximum_full_sl,
                    "selector_minimum_quality_margin": selector_minimum_margin,
                    "ai_engine_v2_enabled": int(ai_engine_v2_enabled),
                    "ai_engine_v2_minimum_score": ai_engine_v2_minimum_score,
                    "ai_engine_v2_minimum_quality_margin": ai_engine_v2_minimum_margin,
                },
            )

        def do_POST(self) -> None:
            nonlocal moment_future, finbert_future, latest_moment_payload
            nonlocal latest_finbert_payload, last_shadow_bar, last_finbert_event_key
            nonlocal latest_market, latest_market_received_utc, ensemble
            nonlocal latest_display_forecast
            if self.path not in {"/decision", "/trades", "/manual-override", "/manual-overrides/reset", "/human-opinion", "/position-exit-advice", "/opportunity-entry-advice", "/reload-roles", "/forecast-only"}:
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 2 or length > 250_000:
                    raise ValueError("invalid body size")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("request must be a JSON object")
                if self.path == "/human-opinion":
                    _persist_human_opinion(history_db, payload)
                    self.reply(200, {"saved": True})
                    return
                if self.path == "/manual-overrides/reset":
                    cleared = []
                    for bar_time, rows in list(manual_overrides_by_bar.items()):
                        for stage, original in list(rows.items()):
                            reset_row = dict(original)
                            reset_row["action"] = "RESET"
                            reset_row["requested_utc"] = int(time.time())
                            reset_row["reset_all"] = True
                            _persist_manual_override(history_db, reset_row)
                            cleared.append({"signal_bar_time": bar_time, "stage": stage})
                    manual_overrides_by_bar.clear()
                    self.reply(200, {"saved": True, "cleared": cleared, "active": []})
                    return

                if self.path == "/manual-override":
                    stage = str(payload.get("stage", ""))
                    sample_key = str(payload.get("sample_key", ""))
                    signal_bar_time = int(payload.get("signal_bar_time", 0))
                    if stage not in ANALYTIC_OVERRIDE_STAGES:
                        raise ValueError("unsupported override stage")
                    if not sample_key or signal_bar_time <= 0:
                        raise ValueError("sample_key and signal_bar_time are required")
                    action = str(payload.get("action", "FORCE_PASS"))
                    if action not in {"FORCE_PASS", "REVIEW_ONLY"}:
                        raise ValueError("unsupported override action")
                    row = {
                        "stage": stage,
                        "sample_key": sample_key,
                        "signal_bar_time": signal_bar_time,
                        "action": action,
                        "requested_utc": int(time.time()),
                        "original_state": payload.get("original_state"),
                        "original_reason": payload.get("original_reason"),
                        "node_values": payload.get("node_values") if isinstance(payload.get("node_values"), dict) else {},
                        "operator": "dashboard",
                    }
                    if action == "FORCE_PASS":
                        manual_overrides_by_bar.setdefault(signal_bar_time, {})[stage] = row
                    _persist_manual_override(history_db, row)
                    self.reply(200, {
                        "saved": True,
                        "action": action,
                        "active": sorted(manual_overrides_by_bar.get(signal_bar_time, {})),
                    })
                    return
                if self.path == "/forecast-only":
                    market = Market.from_dict(payload)
                    quote_time = int(payload["quote_time"]) if "quote_time" in payload else None
                    market.validate_quote_context(quote_time)
                    # Display-only horizon override; Ramon trading horizon is unchanged.
                    horizon = int(payload.get("forecast_horizon_bars", settings.horizon))
                    if horizon < 1 or horizon > 16:
                        raise ValueError("forecast_horizon_bars must be 1..16")
                    closes = [float(bar.close) for bar in market.bars[-settings.context:]]
                    with guard:
                        forecast = cached_model.forecast(closes, horizon)
                    midpoint = (float(market.bid) + float(market.ask)) / 2.0
                    move = float(forecast.median) - midpoint
                    deadband = max(float(market.point) * 5.0, abs(midpoint) * 1e-7)
                    direction = "UP" if move > deadband else "DOWN" if move < -deadband else "FLAT"
                    if horizon == 2:
                        latest_display_forecast = {
                            "model": model.model_id,
                            "symbol": market.symbol,
                            "direction": direction,
                            "current_mid": midpoint,
                            "forecast_step_1": float(forecast.median_path[0]),
                            "forecast_step_2": float(forecast.median_path[1]),
                            "horizon_minutes": 30,
                            "captured_utc": int(time.time()),
                            "advisory_only": True,
                        }
                    self.reply(200, {
                        "model": model.model_id,
                        "direction": direction,
                        "current_mid": midpoint,
                        "forecast_low": forecast.low,
                        "forecast_median": forecast.median,
                        "forecast_high": forecast.high,
                        "forecast_median_path": list(forecast.median_path),
                        "forecast_step_1": float(forecast.median_path[0]),
                        "forecast_step_2": float(forecast.median_path[1]) if horizon >= 2 else float(forecast.median_path[0]),
                        "forecast_horizon_bars": horizon,
                        "forecast_horizon_minutes": horizon * 15,
                        "signal_bar_time": market.bars[-1].time,
                        "advisory_only": True,
                        "ramon_decision_independent": True,
                    })
                    return
                if self.path == "/reload-roles":
                    refreshed = coordinator(ensemble_dir, model.model_id)
                    if refreshed.error:
                        raise ValueError(refreshed.error)
                    ensemble = refreshed
                    self.reply(200, {
                        "reloaded": True,
                        "bundle": ensemble.bundle_id,
                        **ensemble.status(),
                    })
                    return
                if self.path == "/opportunity-entry-advice":
                    if latest_market is None or int(time.time()) - latest_market_received_utc > 120:
                        raise ValueError("fresh market context unavailable")
                    direction = str(payload.get("direction", "")).upper()
                    probability = float(payload.get("success_probability", -1.0))
                    horizon = int(payload.get("horizon", settings.horizon))
                    if direction not in {"BUY", "SELL"}:
                        raise ValueError("direction must be BUY or SELL")
                    if probability < 0.60 or probability > 1.0:
                        raise ValueError("entry advice requires success_probability >= 0.60")
                    if horizon < 1 or horizon > 16:
                        raise ValueError("horizon must be 1..16")
                    closes = [float(bar.close) for bar in latest_market.bars[-settings.context:]]
                    with guard:
                        forecast = cached_model.forecast(closes, horizon)
                    path = [float(x) for x in forecast.median_path]
                    if not path:
                        raise ValueError("Chronos returned an empty median path")
                    current_price = (
                        float(latest_market.ask) if direction == "BUY"
                        else float(latest_market.bid)
                    )
                    if direction == "BUY":
                        candidate_index = min(range(len(path)), key=lambda i: path[i])
                        candidate_price = path[candidate_index]
                        later_best = max(path[candidate_index:])
                        continuation = later_best > candidate_price
                        if candidate_price >= current_price or not continuation:
                            action = "ENTER_NEAR"
                            suggested_price = current_price
                            step = 0
                        else:
                            action = "WAIT_PULLBACK"
                            suggested_price = candidate_price
                            step = candidate_index + 1
                    else:
                        candidate_index = max(range(len(path)), key=lambda i: path[i])
                        candidate_price = path[candidate_index]
                        later_best = min(path[candidate_index:])
                        continuation = later_best < candidate_price
                        if candidate_price <= current_price or not continuation:
                            action = "ENTER_NEAR"
                            suggested_price = current_price
                            step = 0
                        else:
                            action = "WAIT_PULLBACK"
                            suggested_price = candidate_price
                            step = candidate_index + 1
                    self.reply(200, {
                        "model": model.model_id,
                        "direction": direction,
                        "success_probability": probability,
                        "action": action,
                        "suggested_entry_price": suggested_price,
                        "forecast_entry_step": step,
                        "forecast_entry_minutes": step * 15,
                        "current_price": current_price,
                        "median_path": path,
                        "forecast_final_low": forecast.low,
                        "forecast_final_median": forecast.median,
                        "forecast_final_high": forecast.high,
                        "market_bar_time": latest_market.bars[-1].time,
                        "market_context_age_seconds": max(0, int(time.time()) - latest_market_received_utc),
                        "advisory_only": True,
                    })
                    return
                if self.path == "/position-exit-advice":
                    if latest_market is None or int(time.time()) - latest_market_received_utc > 120:
                        raise ValueError("fresh market context unavailable")
                    direction = str(payload.get("direction", "")).upper()
                    entry_price = float(payload.get("entry_price", 0.0))
                    current_price = float(payload.get("current_price", 0.0))
                    horizon = int(payload.get("horizon", settings.horizon))
                    if direction not in {"BUY", "SELL"}:
                        raise ValueError("direction must be BUY or SELL")
                    if entry_price <= 0.0 or current_price <= 0.0:
                        raise ValueError("entry_price and current_price must be positive")
                    if horizon < 1 or horizon > 16:
                        raise ValueError("horizon must be 1..16")
                    closes = [float(bar.close) for bar in latest_market.bars[-settings.context:]]
                    with guard:
                        forecast = cached_model.forecast(closes, horizon)
                    path = [float(x) for x in forecast.median_path]
                    if not path:
                        raise ValueError("Chronos returned an empty median path")
                    sign = 1.0 if direction == "BUY" else -1.0
                    current_progress = sign * (current_price - entry_price)
                    if direction == "BUY":
                        peak_index = max(range(len(path)), key=lambda i: path[i])
                    else:
                        peak_index = min(range(len(path)), key=lambda i: path[i])
                    model_target = path[peak_index]
                    best_progress = sign * (model_target - entry_price)

                    if current_progress >= 0.0:
                        if best_progress <= current_progress:
                            action = "EXIT_NOW"
                            suggested = current_price
                        elif best_progress > 0.0:
                            action = "TARGET"
                            suggested = model_target
                        else:
                            action = "NO_PROFIT_TARGET"
                            suggested = None
                    else:
                        # Losing position: use Chronos' best directional recovery
                        # inside the horizon as a loss-reduction exit. If no
                        # recovery above the current mark is forecast, advise a
                        # near exit rather than inventing a recovery target.
                        if best_progress > current_progress:
                            action = "RECOVERY_EXIT"
                            suggested = model_target
                        else:
                            action = "LOSS_EXIT_NOW"
                            suggested = current_price
                    self.reply(200, {
                        "model": model.model_id,
                        "direction": direction,
                        "entry_price": entry_price,
                        "current_price": current_price,
                        "horizon_bars": horizon,
                        "horizon_minutes": horizon * 15,
                        "action": action,
                        "suggested_exit_price": suggested,
                        "forecast_peak_price": model_target,
                        "forecast_peak_step": peak_index + 1,
                        "current_progress_price": current_progress,
                        "best_progress_price": best_progress,
                        "forecast_final_low": forecast.low,
                        "forecast_final_median": forecast.median,
                        "forecast_final_high": forecast.high,
                        "median_path": path,
                        "market_bar_time": latest_market.bars[-1].time,
                        "market_context_age_seconds": max(0, int(time.time()) - latest_market_received_utc),
                        "advisory_only": True,
                    })
                    return
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
                broker_utc_offset_seconds = (
                    int(payload["broker_utc_offset_seconds"])
                    if payload.get("broker_utc_offset_seconds") is not None else None
                )
                if broker_utc_offset_seconds is not None:
                    if (
                        abs(broker_utc_offset_seconds) > 14 * 3600
                        or broker_utc_offset_seconds % 900 != 0
                    ):
                        raise ValueError("invalid broker UTC offset")
                market.validate_quote_context(quote_time)
                latest_market = market
                latest_market_received_utc = int(time.time())

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
                account_is_cent = payload.get("account_is_cent") is True
                request_settings = (
                    replace(settings, require_direction_confirmation=False)
                    if account_is_cent else settings
                )
                active_override_rows = manual_overrides_by_bar.get(int(market.bars[-1].time), {})
                active_overrides = frozenset(active_override_rows)
                with guard:
                    result = evaluate(
                        market,
                        cached_model,
                        request_settings,
                        manual_overrides=active_overrides,
                    )
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
                response["forecast_horizon_bars"] = settings.horizon
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

                direction_gate = independent_market_direction(market, atr=result.atr)
                timing_gate = independent_entry_timing(
                    market,
                    atr=result.atr,
                    direction=str(direction_gate["direction"]),
                    min_move_atr=settings.trend_min_micro_move_atr,
                    min_rebound_atr=settings.intrabar_min_rebound_atr,
                )
                response.update({
                    "cent_direction_gate_active": int(account_is_cent),
                    "market_direction": direction_gate["direction"],
                    "market_direction_score": direction_gate["score"],
                    "market_direction_ret_1_atr": direction_gate["ret_1_atr"],
                    "market_direction_ret_4_atr": direction_gate["ret_4_atr"],
                    "market_direction_micro_move_atr": direction_gate["micro_move_atr"],
                    "market_direction_structure": direction_gate["structure"],
                    "entry_timing_ready": timing_gate["ready"],
                    "entry_timing_direction": timing_gate["direction"],
                    "entry_timing_move_atr": timing_gate["move_atr"],
                    "entry_timing_rebound_atr": timing_gate["rebound_atr"],
                    "entry_timing_turn": timing_gate["turn"],
                })

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
                if (
                    range_setup is None
                    and "range" in active_overrides
                    and range_live_enabled
                    and payload.get("range_execution_ready") is True
                    and quote_time is not None
                    and (market.ask - market.bid) / market.point <= settings.max_spread_points
                ):
                    forced_range = range_candidate(market)
                    if forced_range.get("range_candidate"):
                        range_setup = forced_range
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
                reversal_selected = apply_reversal(
                    response, result, request_settings, market_assessment,
                    enabled=reversal_live_enabled, manual_overrides=active_overrides,
                )
                ai_engine_authoritative = False
                if (
                    account_is_cent
                    and not bool(response.get("range_execution"))
                    and not reversal_selected
                ):
                    ai_engine_authoritative = apply_ai_decision_engine(
                        response,
                        result,
                        market_assessment,
                        enabled=ai_engine_v2_enabled,
                        minimum_score=ai_engine_v2_minimum_score,
                        minimum_direction_quality=selector_minimum_quality,
                        minimum_quality_margin=selector_minimum_margin,
                        maximum_full_sl_probability=selector_maximum_full_sl,
                        anomaly_soft_threshold=moment_live_threshold,
                        anomaly_hard_threshold=moment_hard_veto_threshold,
                    )

                if ai_engine_authoritative:
                    # AI owns analytical direction/timing/quality. Market-state
                    # hazards remain authoritative because the engine refuses
                    # every assessment route marked WAIT.
                    response.update(
                        market_state=market_assessment["state"],
                        market_state_route="AI_ENGINE_V2",
                        market_state_policy=market_assessment["version"],
                    )
                    if response.get("ai_engine_v2_selected") and response.get("decision") in {"BUY", "SELL"}:
                        live_target = build_target_structure(
                            market,
                            direction=str(response["decision"]),
                            atr=result.atr,
                            target_distance=result.target_distance,
                        )
                        target_payload = live_target.to_dict()
                        response["target_structure_ready"] = live_target.ready
                        response["target_method"] = live_target.method
                        response["target_direction"] = live_target.direction
                        response["target_impulse_start"] = live_target.impulse_start
                        response["target_impulse_end"] = live_target.impulse_end
                        response["target_impulse_range"] = live_target.impulse_range
                        response["target_impulse_atr"] = live_target.impulse_atr
                        response["target_tp1"] = live_target.tp1
                        response["target_tp2"] = live_target.tp2
                        response["target_tp3"] = live_target.tp3
                        response["legacy_target_price"] = live_target.legacy_target
                elif settings.market_state_policy_enabled and not reversal_selected:
                    apply_market_policy(response, market_assessment)
                elif not reversal_selected:
                    response.update(market_state=market_assessment["state"], market_state_route="OBSERVE",
                                    market_state_policy=market_assessment["version"])

                # Legacy selector remains available for rollback/testing, but the
                # AI engine is the primary analytical path when authoritative.
                if (
                    not ai_engine_authoritative
                    and account_is_cent
                    and not bool(response.get("range_execution"))
                ):
                    apply_live_selector(
                        response,
                        result,
                        market_assessment,
                        enabled=selector_live_enabled,
                        minimum_quality=selector_minimum_quality,
                        maximum_full_sl_probability=selector_maximum_full_sl,
                        minimum_quality_margin=selector_minimum_margin,
                        anomaly_ratio=float(response.get("moment_anomaly_ratio", -1.0)),
                        anomaly_soft_threshold=moment_live_threshold,
                    )
                else:
                    response.update({
                        "selector_live_enabled": int(selector_live_enabled),
                        "selector_live_selected": 0,
                        "selector_minimum_quality": selector_minimum_quality,
                        "selector_maximum_full_sl_probability": selector_maximum_full_sl,
                        "selector_minimum_quality_margin": selector_minimum_margin,
                    })

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
                    and moment_ratio >= moment_hard_veto_threshold
                )
                finbert_ready = int(response.get("finbert_shadow_ready", 0)) == 1
                finbert_score = abs(float(response.get("finbert_directional_score", 0.0)))
                # Raw FinBERT sentiment is context, not a directionally reliable
                # XAUUSD hard veto. The trained news specialist is consumed by
                # AI Engine v2 instead.
                finbert_veto = False
                response["moment_live_active"] = int(moment_live_enabled)
                response["moment_live_fresh"] = int(moment_fresh)
                response["moment_live_veto"] = int(moment_veto)
                response["moment_live_threshold"] = moment_live_threshold
                response["moment_hard_veto_threshold"] = moment_hard_veto_threshold
                response["moment_risk_mode"] = "SOFT_PENALTY_WITH_EXTREME_HARD_VETO"
                response["finbert_live_active"] = int(finbert_live_enabled)
                response["finbert_live_veto"] = int(finbert_veto)
                response["finbert_live_threshold"] = finbert_live_threshold
                response["finbert_risk_mode"] = "AI_FEATURE_NO_HARD_VETO"

                if (
                    account_is_cent
                    and not bool(response.get("range_execution"))
                    and not bool(response.get("ai_engine_v2_active"))
                    and str(response.get("decision", "")) in {"BUY", "SELL"}
                ):
                    proposed = str(response["decision"])
                    detected = str(response.get("market_direction", "NEUTRAL"))
                    if detected != proposed and "market_direction" not in active_overrides:
                        response["decision"] = "WAIT"
                        response["reason"] = (
                            "market_direction_neutral"
                            if detected == "NEUTRAL"
                            else "market_direction_conflict"
                        )
                    elif int(response.get("entry_timing_ready", 0)) != 1 and "entry_timing" not in active_overrides:
                        response["decision"] = "WAIT"
                        response["reason"] = "entry_timing_required"

                if str(response.get("decision", "")) in {"BUY", "SELL"}:
                    if moment_veto:
                        response["decision"] = "WAIT"
                        response["reason"] = "moment_anomaly_veto"
                        response["range_execution"] = 0
                    elif finbert_veto:
                        response["decision"] = "WAIT"
                        response["reason"] = "finbert_sentiment_veto"
                        response["range_execution"] = 0

                if "range" in active_overrides and range_setup is not None:
                    response["decision"] = range_setup["direction"]
                    response["reason"] = "manual_override_range_pass"
                    response["range_execution"] = 1
                elif "decision" in active_overrides and str(response.get("decision", "")) == "WAIT":
                    response["decision"] = dominant_direction(result)
                    response["reason"] = "manual_override_final_pass"
                    response["range_execution"] = 0

                if not response["range_execution"]:
                    range_setup = None
                if response["decision"] == "WAIT":
                    response["reversal_execution"] = 0
                sample_key = uuid4().hex[:16]
                response["sample_key"] = sample_key
                response["sample_saved"] = 0
                response["manual_overrides_active"] = sorted(active_overrides)
                response["manual_override_count"] = len(active_overrides)
                response["manual_execution_override"] = int(
                    bool(active_overrides & {"entry_timing", "base", "decision"})
                )

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
                                "execution_strategy": (
                                    "range-reversal-v1" if range_setup
                                    else "confirmed-reversal-v1" if response.get("reversal_execution")
                                    else "ai-engine-v2" if response.get("ai_engine_v2_active")
                                    else "selector-v2" if response.get("selector_live_selected")
                                    else "chronos"
                                ),
                                "range_setup": range_setup,
                                "market_assessment": (
                                    {**market_assessment, "base_route": market_assessment["route"],
                                     "route": "CONFIRMED_REVERSAL", "allowed_directions": [response["decision"]]}
                                    if response.get("reversal_execution") else market_assessment
                                ),
                                "decision_audit": {
                                    "schema_version": 2,
                                    "base": result.to_dict(),
                                    "final": {**ensemble_payload, "decision": response["decision"],
                                              "reason": response["reason"],
                                              "range_execution": response["range_execution"],
                                              "reversal_execution": response.get("reversal_execution", 0),
                                              "reversal_live_enabled": int(reversal_live_enabled),
                                              "market_state": response["market_state"],
                                              "market_state_route": response["market_state_route"],
                                              "cent_direction_gate_active": response.get("cent_direction_gate_active", 0),
                                              "market_direction": response.get("market_direction"),
                                              "market_direction_score": response.get("market_direction_score"),
                                              "market_direction_ret_1_atr": response.get("market_direction_ret_1_atr"),
                                              "market_direction_ret_4_atr": response.get("market_direction_ret_4_atr"),
                                              "market_direction_micro_move_atr": response.get("market_direction_micro_move_atr"),
                                              "market_direction_structure": response.get("market_direction_structure"),
                                              "entry_timing_ready": response.get("entry_timing_ready", 0),
                                              "entry_timing_direction": response.get("entry_timing_direction"),
                                              "entry_timing_move_atr": response.get("entry_timing_move_atr"),
                                              "entry_timing_rebound_atr": response.get("entry_timing_rebound_atr"),
                                              "entry_timing_turn": response.get("entry_timing_turn", 0),
                                              "ai_engine_v2_active": response.get("ai_engine_v2_active", 0),
                                              "ai_engine_v2_selected": response.get("ai_engine_v2_selected", 0),
                                              "ai_engine_v2_score": response.get("ai_engine_v2_score", -1.0),
                                              "ai_engine_v2_raw_score": response.get("ai_engine_v2_raw_score", -1.0),
                                              "ai_engine_v2_candidate_direction": response.get("ai_engine_v2_candidate_direction"),
                                              "ai_engine_v2_score_source": response.get("ai_engine_v2_score_source"),
                                              "ai_engine_v2_quality_margin": response.get("ai_engine_v2_quality_margin", -1.0),
                                              "ai_engine_v2_entry_probability": response.get("ai_engine_v2_entry_probability", -1.0),
                                              "ai_engine_v2_full_sl_probability": response.get("ai_engine_v2_full_sl_probability", -1.0),
                                              "ai_engine_v2_anomaly_penalty": response.get("ai_engine_v2_anomaly_penalty", 0.0),
                                              "manual_overrides_active": sorted(active_overrides),
                                              "manual_override_rows": list(active_override_rows.values()),
                                              "manual_execution_override": response.get("manual_execution_override", 0)},
                                    "settings": asdict(request_settings),
                                    "handlers": model_handlers(),
                                    "external_models": {
                                        "moment": dict(latest_moment_payload),
                                        "finbert": dict(latest_finbert_payload),
                                    },
                                    "news_snapshot": news_snapshot.payload(),
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
                                    forecast=audit_forecast, settings=settings, response={**response, "replay_input_saved": 1},
                                    broker_utc_offset_seconds=broker_utc_offset_seconds))
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
                if response.get("reversal_execution") and not response["sample_saved"]:
                    response["decision"] = "WAIT"
                    response["reason"] = "reversal_sample_not_saved"
                    response["reversal_execution"] = 0
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
