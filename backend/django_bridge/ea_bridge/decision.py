"""Conservative Django-hosted decision API for Ramon EA."""
import hashlib, json, math, time, logging
from decimal import Decimal
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from .auth import BearerTokenRequired
from django.utils import timezone
from .models import AccountDailyBaseline, StrategyDecisionRecord, EAHeartbeat, RiskVerdictRecord

logger = logging.getLogger(__name__)
from ramon_pipeline.signalflow.entry_timing import EntryTimer
from ramon_pipeline.signalflow.pipeline import SignalFlowPipeline

def _num(v, name):
    v=float(v)
    if not math.isfinite(v): raise ValueError(f"{name} must be finite")
    return v

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
        daily_current_equity = None

        heartbeat = None
        if instance_id and account_login:
            heartbeat = EAHeartbeat.objects.filter(instance_id=instance_id).first()

        if heartbeat is not None:
            age = (timezone.now() - heartbeat.last_seen).total_seconds()
            snapshot = heartbeat.payload if isinstance(heartbeat.payload, dict) else {}
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
                    daily_current_equity = snapshot_equity
                    request_balance = _num(p["account_balance"], "request balance")
                except (KeyError, TypeError, ValueError, OverflowError):
                    account_state_reason = "ACCOUNT_BALANCE_UNAVAILABLE"
                else:
                    if snapshot_balance <= 0 or snapshot_equity <= 0 or request_balance <= 0:
                        account_state_reason = "ACCOUNT_BALANCE_INVALID"
                    elif abs(snapshot_balance - request_balance) > 0.01:
                        account_state_reason = "ACCOUNT_BALANCE_MISMATCH"
                    elif positions_snapshot:
                        # Current telemetry lacks enough broker metadata to independently
                        # calculate the risk of every open position.
                        account_state_reason = "OPEN_POSITION_RISK_UNVERIFIED"
                    else:
                        baseline = AccountDailyBaseline.objects.filter(
                            account_login=account_login,
                            server_name=server_name,
                            broker_day=broker_day,
                        ).first()
                        if baseline is None or baseline.opening_balance <= 0:
                            account_state_reason = "DAILY_LOSS_BASELINE_UNAVAILABLE"
                        else:
                            daily_baseline_day = broker_day.isoformat()
                            daily_baseline_balance = float(baseline.opening_balance)
                            # Equity drawdown from the server-recorded opening balance
                            # includes both realized and current floating losses.
                            server_daily_loss_percent = max(
                                0.0,
                                (daily_baseline_balance - snapshot_equity) / daily_baseline_balance * 100.0,
                            )
                            account_state_reason = ""

        risk_guard_fields=("stop_level_points","take_profit_points","daily_loss_percent","open_risk_percent")
        missing_risk_inputs=[name for name in risk_guard_fields if p.get(name) is None]
        risk_guards_complete=not missing_risk_inputs
        market={"symbol":symbol,"timeframe":"M15","bars":bars,"last_bar_closed":True,
          "latest_bar_time":bar_time,"bid":bid,"ask":ask,"point":point,
          "trend":direction,"setup_score":setup,"entry_quality":quality,"spread_points":spread,
          "account_balance":_num(p.get("account_balance",0),"account_balance"),"risk_percent":_num(p.get("risk_percent",0),"risk_percent"),
          "stop_loss_points":atr*1.5/point,"point_value_per_lot":_num(p.get("point_value_per_lot",0),"point_value_per_lot"),
          "volume_step":_num(p.get("volume_step",0),"volume_step"),"min_volume":_num(p.get("min_volume",0),"min_volume"),
          "max_volume":_num(p.get("max_volume",0),"max_volume")}
        for name in risk_guard_fields:
            market[name]=_num(p[name],name) if p.get(name) is not None else None
        # The client value is required for protocol compatibility, but never used to
        # authorize trading. The daily-loss guard receives only the server-derived value.
        market["daily_loss_percent"] = server_daily_loss_percent
        result=SignalFlowPipeline(entry_timer=EntryTimer(require_quote=True)).run(market)
        if not risk_guards_complete:
            action = "WAIT"
            reason = "RISK_INPUTS_INCOMPLETE"
        elif account_state_reason:
            action = "WAIT"
            reason = account_state_reason
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
          "structural_candle_count": len(bars),
          "market_snapshot": {
            "symbol": symbol,
            "timeframe": "M15",
            "bar_time": bar_time,
            "bid": bid,
            "ask": ask,
            "point": point,
          },
          "daily_risk": {
            "baseline_available": server_daily_loss_percent is not None,
            "broker_day": daily_baseline_day,
            "opening_balance": daily_baseline_balance,
            "current_equity": daily_current_equity,
            "loss_percent": server_daily_loss_percent,
            "source": "SERVER_BASELINE_EQUITY_DRAWDOWN" if server_daily_loss_percent is not None else "UNAVAILABLE_FAIL_CLOSED",
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
                tp_points = Decimal(str(p["take_profit_points"]))
                point_value = Decimal(str(point))
                entry_value = Decimal(str(ask if record.action == "BUY" else bid))
                take_profit_price = entry_value + tp_points * point_value * (1 if record.action == "BUY" else -1)
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
        RiskVerdictRecord.objects.create(
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
                "server_daily_loss_percent": server_daily_loss_percent,
                "risk_stage": risk_stage,
            },
        )
        response = _compat(
          record.action, record.rationale_code, key, bar_time, bid, ask,
          atr, median, spread, confidence, risk_approved,
        )
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
        response["daily_loss_source"] = "SERVER_BASELINE_EQUITY_DRAWDOWN" if server_daily_loss_percent is not None else "UNAVAILABLE_FAIL_CLOSED"
        response["stages"] = result["stages"]
        return JsonResponse(response, json_dumps_params={"separators": (",", ":")})
    except (KeyError,TypeError,ValueError,OverflowError,json.JSONDecodeError) as exc:
        logger.warning("Ramon decision request rejected: %s", exc)
        return JsonResponse({"ok":False,"error":str(exc)},status=400)
