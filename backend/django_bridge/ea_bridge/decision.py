"""Conservative Django-hosted decision API for Ramon EA."""
import hashlib, json, math, time, logging
from decimal import Decimal
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from .auth import BearerTokenRequired
from django.utils import timezone
from .models import AccountDailyBaseline, StrategyDecisionRecord, EAHeartbeat, RiskVerdictRecord, DecisionThresholdConfig
from .thresholds import defaults_for

logger = logging.getLogger(__name__)
import threading
from collections import OrderedDict
from ramon_pipeline.signalflow.entry_timing import EntryTimer, EntryTimingConfig
from ramon_pipeline.signalflow.opportunities import OpportunityConfig, OpportunityDetector
from ramon_pipeline.signalflow.pipeline import SignalFlowPipeline
from ramon_pipeline.signalflow.risk_management import RiskConfig, RiskManager

# Long-lived duplicate-signal memory. A detector created per request starts with an empty
# memory, so dedupe only works when this shared store is injected (RAMON_DEDUPE_SIGNALS=1).
# It is per worker process: with several gunicorn workers use one worker or a shared store.
_SIGNAL_MEMORY = OrderedDict()
_SIGNAL_MEMORY_LOCK = threading.Lock()


def _build_pipeline(*, demo_mode=False):
    """Build the Django-owned pipeline from persisted profile-specific thresholds.

    Only strategy tuning values are editable. Account risk ceilings, daily-loss
    protections, structural validation, quote requirements and execution guards
    remain code-owned hard protections.
    """
    def opt(name, default):
        return getattr(settings, name, default)

    dedupe = bool(opt("RAMON_DEDUPE_SIGNALS", False))
    relaxed = (
        bool(demo_mode)
        and str(opt("RAMON_DEMO_PROFILE", "expanded")).strip().lower() != "standard"
    )
    profile = "demo" if relaxed else "standard"
    threshold_values = defaults_for(profile)
    saved = DecisionThresholdConfig.objects.filter(profile=profile).first()
    if saved and isinstance(saved.values, dict):
        for group, updates in saved.values.items():
            if group in threshold_values and isinstance(updates, dict):
                threshold_values[group].update({
                    key: value for key, value in updates.items()
                    if key in threshold_values[group]
                })

    opportunity_values = dict(threshold_values["opportunities"])
    minimum_score = float(opportunity_values.pop("minimum_score"))
    entry_values = dict(threshold_values["entry_timing"])
    minimum_quality = float(entry_values.pop("minimum_quality"))
    maximum_spread_points = float(entry_values.pop("maximum_spread_points"))
    risk_values = dict(threshold_values["risk_management"])

    detector = OpportunityDetector(
        minimum_score=minimum_score,
        demo_mode=demo_mode,
        config=OpportunityConfig(
            **opportunity_values,
            require_closed_flag=True,
            impulse_start_mode=opt("RAMON_IMPULSE_START_MODE", "LAST_SWING_LOW"),
            # LiteFinance publishes XAUUSD sessions in broker-server time.
            session_timezone=opt("RAMON_MARKET_SESSION_TIMEZONE", "Europe/Helsinki"),
            session_hours=opt(
                "RAMON_MARKET_SESSION_HOURS",
                (
                    (0, 1, 6, 23, 59),
                    (1, 1, 2, 23, 59),
                    (2, 1, 2, 23, 59),
                    (3, 1, 2, 23, 59),
                    (4, 1, 2, 23, 59),
                ),
            ),
        ),
        accepted_store=_SIGNAL_MEMORY if dedupe else None,
        accepted_lock=_SIGNAL_MEMORY_LOCK if dedupe else None,
    )
    timer = EntryTimer(
        minimum_quality=minimum_quality,
        maximum_spread_points=maximum_spread_points,
        require_quote=True,
        config=EntryTimingConfig(
            **entry_values,
            require_signal_time=True,
            ignore_supplied_quality=bool(opt("RAMON_USE_COMPUTED_ENTRY_QUALITY", False)),
        ),
    )
    # EntryTimer preserves legacy file-based defaults when passed its historical
    # constructor defaults; explicitly pin the Django-persisted values afterwards.
    timer.minimum_quality = minimum_quality
    timer.maximum_spread_points = maximum_spread_points
    maximum_risk_percent = float(risk_values.pop("maximum_risk_percent"))
    risk = RiskManager(
        maximum_risk_percent=maximum_risk_percent,
        config=RiskConfig(
            **risk_values,
            strict_structural=True,
            short_stop_spread_adjust=bool(opt("RAMON_SHORT_STOP_SPREAD_ADJUST", False)),
        )
    )
    return SignalFlowPipeline(opportunities=detector, entry_timer=timer, risk_manager=risk)

def _num(v, name):
    v=float(v)
    if not math.isfinite(v): raise ValueError(f"{name} must be finite")
    return v

def _apply_demo_risk_policy(market, pipeline, *, demo_mode):
    """Apply the server-owned demo risk target; never raise the live account's request."""
    requested = 0.0
    maximum = _num(pipeline.risk_manager.maximum_risk_percent, "maximum_risk_percent")
    if maximum <= 0:
        raise ValueError("maximum_risk_percent must be positive")
    if not demo_mode:
        market["risk_percent"] = 0.0
        return {
            "source": "SERVER_POLICY",
            "requested_risk_percent": requested,
            "effective_risk_percent": requested,
            "maximum_risk_percent": maximum,
        }

    # The persisted Risk Management dashboard value is the demo per-trade budget
    # and the RiskManager hard ceiling. No EA-supplied value can raise it.
    effective = maximum
    market["risk_percent"] = effective
    return {
        "source": "SERVER_DEMO_POLICY",
        "requested_risk_percent": requested,
        "effective_risk_percent": effective,
        "maximum_risk_percent": maximum,
    }

def _ema(values, period):
    alpha=2.0/(period+1); out=values[0]
    for v in values[1:]: out=alpha*v+(1-alpha)*out
    return out

def _compat(decision, reason, key, bar_time, bid, ask, atr, median, spread, confidence, risk_approved):
    numeric="""ensemble_ready ensemble_active news_probability meta_probability risk_model_ready news_source_ready news_model_ready
    intrabar_confirmed intrabar_move_atr intrabar_rebound_atr intrabar_min_strength intrabar_min_move_atr intrabar_min_rebound_atr
    ai_trend_confirmed ai_trend_score ai_trend_move_atr ai_trend_consistency trend_min_path_atr trend_min_consistency
    trend_min_edge_fraction trend_min_micro_move_atr manual_execution_override ai_engine_v2_selected target_learning_active target_structure_ready""".split()
    d={k:0 for k in numeric}
    d.update({"ok":True,"protocol":"ramon-django-decision-v1","decision":decision,"reason":reason,"sample_key":key,"sample_saved":1,
    "ensemble_bundle":"django-signalflow-technical-v1","base_decision":decision,"base_reason":reason,
    "regime_probability":confidence,"entry_probability":confidence,"risk_probability":-1,"risk_multiplier":1,
    "news_source":"UNAVAILABLE_FAIL_CLOSED","news_source_age_seconds":-1,"news_event_title":"","news_event_country":"",
    "news_event_impact":"","news_event_time":0,"news_event_delta_minutes":0,"signal_bar_time":bar_time,"signal_bid":bid,"signal_ask":ask,
    "forecast_low":median-atr*.5,"forecast_median":median,"forecast_high":median+atr*.5,"atr":atr,
    "edge":median-(bid+ask)/2,"buy_edge":max(0,median-(bid+ask)/2),"sell_edge":max(0,(bid+ask)/2-median),
    "minimum_edge":atr*.15,"uncertainty":atr*.5,"signal_strength":confidence,"minimum_strength":.2,
    "intrabar_direction":"NONE","ai_trend_direction":"NONE","ai_engine_v2_buy_quality":-1,"ai_engine_v2_sell_quality":-1,
    "ai_engine_v2_quality_margin":-1,"ai_engine_v2_score":-1,"ai_engine_v2_direction_source":"NONE","spread_points":spread,
    "stop_distance":atr*1.5,"target_distance":atr*2,"target_method":"ATR_FALLBACK_NOT_TRADE_AUTHORIZATION",
    "target_direction":decision if decision in ("BUY","SELL") else "NONE","target_impulse_start":0,"target_impulse_end":0,
    "target_impulse_range":0,"target_impulse_atr":0,"target_tp1":0,"target_tp2":0,"target_tp3":0,"legacy_target_price":0,
    "forecast_model_handler":"django-signalflow-technical-v1","forecast_experimental_model_handler":"OFF",
    "regime_model_handler":"django-signalflow-technical-v1","entry_model_handler":"signalflow-entry-timing",
    "news_model_handler":"UNAVAILABLE_FAIL_CLOSED","meta_model_handler":"OFF","risk_model_handler":"signalflow-risk-manager",
    "news_source_handler":"UNAVAILABLE_FAIL_CLOSED","market_state_handler":"signalflow-ema-atr","target_model_handler":"atr-fallback",
    "anomaly_model_handler":"OFF","news_sentiment_model_handler":"OFF","direction_quality_live":0,"full_sl_probability":-1,
    "regime_label":"TREND" if decision in ("BUY","SELL") else "UNAVAILABLE",
    "timesfm3_experimental_ready":0,"timesfm3_experimental_direction":"UNAVAILABLE","timesfm3_experimental_low":-1,
    "timesfm3_experimental_median":-1,"timesfm3_experimental_high":-1,"timesfm3_experimental_move_atr":-1,
    "timesfm3_experimental_agrees_chronos":0,"market_state":"EMA_ATR_BASELINE",
    "market_state_route":"SIGNALFLOW" if decision in ("BUY","SELL") else "HOLD",
    "market_state_policy":"NEWS_AND_EA_GATES_REMAIN_MANDATORY","risk_target":"atr-fallback",
    "moment_ready":0,"moment_anomaly_score":0,"moment_anomaly_ratio":0,"moment_anomaly_label":"UNAVAILABLE",
    "finbert_ready":0,"finbert_sentiment_label":"UNAVAILABLE","finbert_directional_score":0,
    "moment_live_active":0,"moment_live_fresh":0,"moment_live_veto":0,"moment_live_threshold":0,
    "finbert_live_active":0,"finbert_live_veto":0,"finbert_live_threshold":0,"news_high_event_time":0,
    "strong_entry_min_intrabar_move_atr":0,"strong_entry_guard_active":0,"recent_move_atr":0,"aligned_recent_move_atr":0,
    "trend_conflict_active":0,"trend_conflict_override_strength":0,"trend_conflict_override_passed":0,
    "intrabar_turn_confirmed":0,"trend_edge_floor":0,"range_execution":0,"opportunity_live_selected":0,
    "execution_enabled":False,"risk_approved_advisory_only":risk_approved,"trading_authorization":False})
    return d

@csrf_exempt
@require_POST
def decision(request):
    denied=BearerTokenRequired.reject_if_invalid(request)
    if denied: return denied
    if not settings.RAMON_DJANGO_DECISION_ENABLED: return JsonResponse({"ok":False,"error":"Django decision API disabled"},status=503)
    if len(request.body)>settings.EA_MAX_BODY_BYTES: return JsonResponse({"ok":False,"error":"request body too large"},status=413)
    try:
        p=json.loads(request.body or b"{}")
        if not isinstance(p,dict): raise ValueError("JSON object required")
        symbol=str(p.get("symbol","")).strip()[:32]; bars=p.get("bars")
        if not symbol or str(p.get("timeframe","")).upper()!="M15" or not isinstance(bars,list) or not 128<=len(bars)<=512:
            raise ValueError("symbol, M15 timeframe, and 128-512 completed bars are required")
        closes=[]; highs=[]; lows=[]; bar_times=[]
        for b in bars:
            if not isinstance(b,dict): raise ValueError("bar entries must be objects")
            stamp=_num(b["time"],"bar time")
            if stamp<=0 or not stamp.is_integer(): raise ValueError("bar time must be a positive integer Unix timestamp")
            bar_times.append(int(stamp))
            closes.append(_num(b["close"],"close")); highs.append(_num(b["high"],"high")); lows.append(_num(b["low"],"low"))
            if lows[-1]<=0 or highs[-1]<lows[-1] or not lows[-1]<=closes[-1]<=highs[-1]:
                raise ValueError("bar OHLC values are inconsistent")
        if any(b<=a for a,b in zip(bar_times,bar_times[1:])):
            raise ValueError("bar timestamps must be strictly increasing")
        bar_time=bar_times[-1]; bid=_num(p["bid"],"bid"); ask=_num(p["ask"],"ask"); point=_num(p["point"],"point")
        now=int(time.time())
        if bid<=0 or ask<bid or point<=0: raise ValueError("invalid quote")
        if bar_time>now+60:
            raise ValueError(
                f"latest bar timestamp is in the future: "
                f"bar_time={bar_time}, now={now}, ahead_seconds={bar_time-now}"
            )
        if now-bar_time>3*900: raise ValueError("latest closed M15 bar is stale")
        # Optional server-side proof that the newest candle has closed (bar_time is its OPEN time).
        # Only enable when bar times and this server's clock use the same time base (see the
        # broker-time offset note in the README), otherwise every request would be rejected.
        if getattr(settings,"RAMON_ENFORCE_BAR_CLOSE",False) and now<bar_time+900:
            raise ValueError("latest M15 bar is still forming")
        ranges=[max(highs[i]-lows[i],abs(highs[i]-closes[i-1]),abs(lows[i]-closes[i-1])) for i in range(1,len(bars))]
        atr=sum(ranges[-14:])/min(14,len(ranges))
        if not math.isfinite(atr) or atr<=0: raise ValueError("invalid ATR")
        fast=_ema(closes[-80:],20); slow=_ema(closes[-150:],50)
        direction="BUY" if fast>slow else "SELL" if fast<slow else "HOLD"
        setup=min(1.0,abs(fast-slow)/(atr*.8)); quality=min(1.0,abs(closes[-1]-closes[-5])/(atr*1.5))
        spread=(ask-bid)/point
        # Strict API-only account-state validation. Legacy SignalFlow callers are unchanged.
        instance_id = str(p.get("instance_id", "")).strip()
        account_login = str(p.get("account_login", "")).strip()
        server_name = str(p.get("server_name", "")).strip()
        account_state_reason = "ACCOUNT_IDENTITY_MISSING"
        server_daily_loss_percent = None
        daily_baseline_day = ""
        daily_baseline_balance = None
        daily_baseline_capture_method = ""
        daily_loss_source = "UNAVAILABLE_FAIL_CLOSED"
        daily_current_equity = None
        account_snapshot_balance = None

        heartbeat = None
        account_snapshot = {}
        positions_snapshot = []
        is_demo_account = False
        if instance_id and account_login:
            heartbeat = EAHeartbeat.objects.filter(instance_id=instance_id).first()

        if heartbeat is not None:
            age = (timezone.now() - heartbeat.last_seen).total_seconds()
            snapshot = heartbeat.payload if isinstance(heartbeat.payload, dict) else {}
            is_demo_account = (
                type(snapshot.get("account_trade_mode")) is int
                and snapshot.get("account_trade_mode") == 0
            )
            account_snapshot = snapshot.get("account", {})
            positions_snapshot = snapshot.get("positions", [])
            broker_day_raw = snapshot.get("broker_day")
            snapshot_server_name = str(snapshot.get("server_name", "")).strip()

            if age < 0 or age > 90:
                account_state_reason = "ACCOUNT_TELEMETRY_STALE"
            elif heartbeat.account_login != account_login:
                account_state_reason = "ACCOUNT_LOGIN_MISMATCH"
            elif not server_name or heartbeat.server_name != server_name or snapshot_server_name != server_name:
                account_state_reason = "ACCOUNT_SERVER_MISMATCH"
            elif heartbeat.symbol != symbol:
                account_state_reason = "ACCOUNT_SYMBOL_MISMATCH"
            elif heartbeat.status != "TELEMETRY_OK" or not heartbeat.terminal_connected:
                account_state_reason = "ACCOUNT_TELEMETRY_NOT_READY"
            elif not isinstance(account_snapshot, dict) or not isinstance(positions_snapshot, list):
                account_state_reason = "ACCOUNT_SNAPSHOT_INVALID"
            elif not isinstance(broker_day_raw, str):
                account_state_reason = "DAILY_LOSS_BASELINE_UNAVAILABLE"
            else:
                try:
                    from datetime import date
                    broker_day = date.fromisoformat(broker_day_raw)
                    if broker_day.isoformat() != broker_day_raw:
                        raise ValueError("invalid broker day")
                    snapshot_balance = _num(account_snapshot["balance"], "telemetry balance")
                    snapshot_equity = _num(account_snapshot["equity"], "telemetry equity")
                    account_snapshot_balance = snapshot_balance
                    daily_current_equity = snapshot_equity

                except (KeyError, TypeError, ValueError, OverflowError):
                    account_state_reason = "ACCOUNT_BALANCE_UNAVAILABLE"
                else:
                    if snapshot_balance <= 0 or snapshot_equity <= 0:
                        account_state_reason = "ACCOUNT_BALANCE_INVALID"
                    else:
                        baseline = AccountDailyBaseline.objects.filter(
                            account_login=account_login,
                            server_name=server_name,
                            broker_day=broker_day,
                        ).first()
                        if baseline is not None and baseline.opening_balance > 0:
                            daily_baseline_balance = float(baseline.opening_balance)
                            daily_baseline_capture_method = baseline.capture_method
                            daily_loss_source = "SERVER_BASELINE_EQUITY_DRAWDOWN"
                        else:
                            # Demo-only fallback: use the EA's broker-history-derived opening
                            # balance only when MT5 explicitly confirms complete history.
                            # This preserves the daily-loss guard without trusting client inputs.
                            opening_balance_raw = snapshot.get("broker_day_opening_balance")
                            history_complete = snapshot.get("broker_day_history_complete") is True
                            try:
                                telemetry_opening_balance = _num(
                                    opening_balance_raw, "broker day opening balance"
                                )
                            except (TypeError, ValueError, OverflowError):
                                telemetry_opening_balance = 0.0
                            if (
                                is_demo_account
                                and history_complete
                                and snapshot.get("server_name") == server_name
                                and telemetry_opening_balance > 0
                            ):
                                daily_baseline_balance = telemetry_opening_balance
                                daily_baseline_capture_method = "BROKER_HISTORY_TELEMETRY_DEMO"
                                daily_loss_source = "BROKER_HISTORY_TELEMETRY_DEMO"
                            elif is_demo_account:
                                # Explicitly aggressive Demo-only fallback: do not block
                                # discovery/execution solely because today's baseline and
                                # complete broker history are unavailable. Daily-loss
                                # enforcement is unavailable for this session and is
                                # recorded as such; all other risk gates remain mandatory.
                                daily_baseline_day = broker_day.isoformat()
                                daily_baseline_capture_method = "DEMO_NO_BASELINE_BYPASS"
                                daily_loss_source = "DEMO_NO_BASELINE_BYPASS"
                                server_daily_loss_percent = None
                                account_state_reason = ""
                            else:
                                account_state_reason = "DAILY_LOSS_BASELINE_UNAVAILABLE"

                        if daily_baseline_balance is not None:
                            daily_baseline_day = broker_day.isoformat()
                            # Equity drawdown includes realized and current floating losses.
                            server_daily_loss_percent = max(
                                0.0,
                                (daily_baseline_balance - snapshot_equity) / daily_baseline_balance * 100.0,
                            )
                            account_state_reason = ""


        # Derive all policy inputs on the server from raw account and symbol telemetry.
        snapshot = heartbeat.payload if heartbeat is not None and isinstance(heartbeat.payload, dict) else {}
        symbol_info = snapshot.get("symbol_info", {}) if isinstance(snapshot.get("symbol_info", {}), dict) else {}
        try:
            if not isinstance(account_snapshot, dict) or not isinstance(positions_snapshot, list):
                raise ValueError("account or positions telemetry unavailable")
            tick_size = _num(symbol_info.get("tick_size"), "symbol tick_size")
            tick_value = _num(symbol_info.get("tick_value"), "symbol tick_value")
            min_volume = _num(symbol_info.get("volume_min"), "symbol volume_min")
            max_volume = _num(symbol_info.get("volume_max"), "symbol volume_max")
            volume_step = _num(symbol_info.get("volume_step"), "symbol volume_step")
            stop_level_points = _num(symbol_info.get("stops_level_points"), "symbol stops level")
            if min(tick_size, tick_value, min_volume, max_volume, volume_step) <= 0:
                raise ValueError("invalid symbol metadata")
            point_value_per_lot = tick_value * point / tick_size
            open_risk_money = 0.0
            for pos in positions_snapshot:
                if not isinstance(pos, dict):
                    raise ValueError("invalid position telemetry")
                vol = _num(pos.get("volume"), "position volume")
                entry = _num(pos.get("price_open"), "position open")
                sl = _num(pos.get("sl"), "position stop")
                pos_tick_size = _num(pos.get("tick_size"), "position tick size")
                pos_tick_value = _num(pos.get("tick_value"), "position tick value")
                pos_type = int(pos.get("type"))
                if pos_type not in (0, 1):
                    raise ValueError("unsupported position type")
                if min(vol, entry, sl, pos_tick_size, pos_tick_value) <= 0:
                    raise ValueError("position risk facts unavailable")
                adverse = entry - sl if pos_type == 0 else sl - entry
                if adverse < 0:
                    raise ValueError("position stop is not loss-side")
                open_risk_money += adverse / pos_tick_size * pos_tick_value * vol
            risk_balance = _num(account_snapshot.get("balance"), "risk balance")
            open_risk_percent_server = open_risk_money / risk_balance * 100.0
            take_profit_points_server = max(stop_level_points + 1.0, 2.0 * atr / point)
        except (TypeError, ValueError, OverflowError):
            account_state_reason = account_state_reason or "SERVER_RISK_INPUTS_UNAVAILABLE"
            point_value_per_lot = 0.0
            min_volume = max_volume = volume_step = stop_level_points = 0.0
            open_risk_percent_server = 100.0
            take_profit_points_server = 0.0

        risk_guard_fields = ("stop_level_points", "take_profit_points", "daily_loss_percent", "open_risk_percent")
        risk_guards_complete = bool(
            heartbeat is not None and not account_state_reason and
            point_value_per_lot > 0 and min_volume > 0 and max_volume >= min_volume and
            volume_step > 0 and take_profit_points_server > 0 and
            _num(account_snapshot.get("balance", 0), "account balance") > 0
        )
        missing_risk_inputs = [] if risk_guards_complete else ["server_symbol_metadata", "server_position_risk", "server_account_state"]
        # Build the profile-specific Django pipeline before constructing its risk inputs.
        pipeline = _build_pipeline(demo_mode=is_demo_account)
        market={"symbol":symbol,"timeframe":"M15","bars":bars,"last_bar_closed":True,
          "latest_bar_time":bar_time,"bid":bid,"ask":ask,"point":point,
          "trend":direction,"setup_score":setup,"entry_quality":quality,"spread_points":spread,
          "account_balance":_num(account_snapshot.get("balance",0),"account_balance"),"risk_percent":0.0,
          "stop_loss_points":atr*1.5/point,"point_value_per_lot":point_value_per_lot,
          "volume_step":volume_step,"min_volume":min_volume,"max_volume":max_volume,
          "stop_level_points":stop_level_points,"take_profit_points":take_profit_points_server,
          "target_reward_risk":pipeline.risk_manager.config.target_reward_risk,
          "daily_loss_percent":server_daily_loss_percent if server_daily_loss_percent is not None else 0.0,
          "open_risk_percent":open_risk_percent_server}
        # The client value is required for protocol compatibility, but never used to
        # authorize trading. The daily-loss guard receives only the server-derived value.
        # SignalFlow requires a numeric value. Zero is only a pipeline placeholder
        # when the Demo baseline is unavailable; the audit record remains None and the
        # command producer independently enforces the same explicit Demo-only bypass.
        market["daily_loss_percent"] = (
            server_daily_loss_percent if server_daily_loss_percent is not None else 0.0
        )
        configured_demo_profile = str(
            getattr(settings, "RAMON_DEMO_PROFILE", "expanded")
        ).strip().lower()
        strategy_profile = (
            "DEMO_EXPANDED"
            if is_demo_account and configured_demo_profile != "standard"
            else "STANDARD"
        )
        risk_policy = _apply_demo_risk_policy(market, pipeline, demo_mode=is_demo_account)
        result = pipeline.run(market)
        if account_state_reason:
            action = "WAIT"
            reason = account_state_reason
        elif not risk_guards_complete:
            action = "WAIT"
            reason = "RISK_INPUTS_INCOMPLETE"
        elif result["approved"]:
            action = result["action"]
            reason = "SIGNALFLOW_APPROVED"
        else:
            action = "WAIT"
            reason = next((s.get("reason","SIGNALFLOW_REJECTED") for s in
              (result["stages"]["opportunities"],result["stages"]["entry_timing"],result["stages"]["risk_management"])
              if not s.get("approved",False)),"SIGNALFLOW_REJECTED")
        key=hashlib.sha256(f"{symbol}:{bar_time}:django-signalflow-v1".encode()).hexdigest()[:16]
        confidence=max(0.0,min(1.0,float(result["stages"]["opportunities"].get("score",min(setup,quality)))))
        median=closes[-1]+(atr*.1 if action=="BUY" else -atr*.1 if action=="SELL" else 0)
        features = {
          "ema20": fast,
          "ema50": slow,
          "atr": atr,
          "setup_score": setup,
          "entry_quality": quality,
          "spread_points": spread,
          "approved": bool(result["approved"]),
          "stages": result["stages"],
          "opportunity_input_path": "completed_ohlc_structural",
          "strategy_profile": strategy_profile,
          "risk_policy": risk_policy,
          "structural_candle_count": len(bars),
          "market_snapshot": {
            "symbol": symbol,
            "timeframe": "M15",
            "bar_time": bar_time,
            "bid": bid,
            "ask": ask,
            "point": point,
          },
          "account_snapshot": {
            "balance": account_snapshot_balance,
            "equity": daily_current_equity,
            "broker_day": daily_baseline_day,
          },
          "daily_risk": {
            "baseline_available": server_daily_loss_percent is not None,
            "broker_day": daily_baseline_day,
            "opening_balance": daily_baseline_balance,
            "capture_method": daily_baseline_capture_method,
            "current_equity": daily_current_equity,
            "loss_percent": server_daily_loss_percent,
            "source": daily_loss_source,
          },
          # Read-only inputs for explaining the existing Django risk verdict in the dashboard.
          # These fields do not participate in, or alter, the decision.
          "risk_calculation_inputs": {
            "account_balance": market["account_balance"],
            "risk_percent": market["risk_percent"],
            "stop_loss_points": market["stop_loss_points"],
            "point_value_per_lot": market["point_value_per_lot"],
            "volume_step": market["volume_step"],
            "min_volume": market["min_volume"],
            "max_volume": market["max_volume"],
            "stop_level_points": market["stop_level_points"],
            "take_profit_points": result["stages"]["risk_management"].get("take_profit_points", market["take_profit_points"]),
            "target_reward_risk": market["target_reward_risk"],
            "daily_loss_percent": server_daily_loss_percent,
            "open_risk_percent": market["open_risk_percent"],
          },
        }
        record, created = StrategyDecisionRecord.objects.update_or_create(
          decision_id=key,
          defaults={
            "symbol": symbol,
            "action": action,
            "confidence": confidence,
            "rationale_code": reason,
            "model_version": "django-signalflow-structural-v2",
            "features": features,
          },
        )
        # Persist a separate, auditable risk verdict. It is never an order command
        # and deliberately cannot grant execution authorization.
        risk_stage = result["stages"].get("risk_management", {})
        risk_approved = bool(
            record.action in ("BUY", "SELL")
            and result.get("approved")
            and risk_stage.get("approved")
            and risk_guards_complete
            and not account_state_reason
        )
        stop_price = risk_stage.get("stop_price")
        take_profit_price = None
        approved_volume = Decimal("0")
        stop_loss_value = None
        take_profit_value = None
        verdict_reason = record.rationale_code or "RISK_REJECTED"
        if risk_approved:
            try:
                approved_volume = Decimal(str(risk_stage["volume"]))
                stop_loss_value = Decimal(str(stop_price))
                entry_value = Decimal(str(ask if record.action == "BUY" else bid))
                target_reward_risk = Decimal(str(risk_stage.get("target_reward_risk", pipeline.risk_manager.config.target_reward_risk)))
                minimum_reward_risk = Decimal(str(pipeline.risk_manager.config.min_reward_risk))
                if target_reward_risk < minimum_reward_risk or minimum_reward_risk < Decimal("1.2"):
                    raise ValueError("target reward/risk must meet the configured minimum of at least 1.2")
                stop_distance = abs(entry_value - stop_loss_value)
                take_profit_price = entry_value + stop_distance * target_reward_risk * (1 if record.action == "BUY" else -1)
                if approved_volume <= 0 or stop_loss_value <= 0 or take_profit_price <= 0:
                    raise ValueError("non-positive executable risk values")
                take_profit_value = take_profit_price
                verdict_reason = "RISK_APPROVED_ADVISORY_ONLY"
            except (KeyError, TypeError, ValueError, ArithmeticError):
                risk_approved = False
                approved_volume = Decimal("0")
                stop_loss_value = None
                take_profit_value = None
                verdict_reason = "RISK_VERDICT_INCOMPLETE"
        verdict = RiskVerdictRecord.objects.create(
            decision_id=key,
            approved=risk_approved,
            reason_code=verdict_reason[:128],
            approved_volume=approved_volume,
            stop_loss=stop_loss_value,
            take_profit=take_profit_value,
            details={
                "execution_authorized": False,
                "command_created": False,
                "signal_action": record.action,
                "risk_inputs_complete": risk_guards_complete,
                "account_state_reason": account_state_reason,
                "daily_baseline_day": daily_baseline_day,
                "daily_baseline_balance": daily_baseline_balance,
                "daily_baseline_capture_method": daily_baseline_capture_method,
                "server_daily_loss_percent": server_daily_loss_percent,
                "risk_stage": risk_stage,
            },
        )
        command = None
        command_created = False
        command_reason = "RISK_VERDICT_NOT_APPROVED" if not risk_approved else "COMMAND_CREATION_DISABLED"
        if risk_approved:
            from .command_producer import create_open_command
            command, command_created, command_reason = create_open_command(
                decision=record,
                verdict=verdict,
                instance_id=instance_id,
                account_login=account_login,
                server_name=server_name,
            )
            if command is not None:
                verdict.details["command_created"] = True
                verdict.save(update_fields=["details"])
        response = _compat(
          record.action, record.rationale_code, key, bar_time, bid, ask, atr, median,
          spread, confidence, risk_approved,
        )
        response["strategy_profile"] = strategy_profile
        response["risk_policy"] = risk_policy
        response["command_created"] = command_created
        response["command_available"] = bool(command is not None and command.state == "PENDING" and command.expires_at > timezone.now())
        response["command_reason"] = command_reason
        response["command_id"] = command.command_id if command is not None else ""
        response["decision_record_created"] = created
        response["risk_inputs_complete"] = risk_guards_complete
        response["missing_risk_inputs"] = ",".join(missing_risk_inputs)
        response["risk_inputs_received"] = ",".join(
          name for name in risk_guard_fields if name not in missing_risk_inputs
        )
        response["daily_baseline_available"] = server_daily_loss_percent is not None
        response["daily_baseline_day"] = daily_baseline_day
        response["daily_baseline_balance"] = daily_baseline_balance
        response["server_daily_loss_percent"] = server_daily_loss_percent
        response["daily_loss_source"] = daily_loss_source
        response["stages"] = result["stages"]
        return JsonResponse(response, json_dumps_params={"separators": (",", ":")})
    except (KeyError,TypeError,ValueError,OverflowError,json.JSONDecodeError) as exc:
        logger.warning("Ramon decision request rejected: %s", exc)
        return JsonResponse({"ok":False,"error":str(exc)},status=400)
