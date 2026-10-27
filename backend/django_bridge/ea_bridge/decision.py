"""Conservative Django-hosted decision API for Ramon EA."""
import hashlib, json, math
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from .auth import BearerTokenRequired
from .models import StrategyDecisionRecord
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
        closes=[]; highs=[]; lows=[]
        for b in bars:
            if not isinstance(b,dict): raise ValueError("bar entries must be objects")
            closes.append(_num(b["close"],"close")); highs.append(_num(b["high"],"high")); lows.append(_num(b["low"],"low"))
            if highs[-1]<lows[-1]: raise ValueError("bar high must be >= low")
        bar_time=int(bars[-1]["time"]); bid=_num(p["bid"],"bid"); ask=_num(p["ask"],"ask"); point=_num(p["point"],"point")
        if bid<=0 or ask<bid or point<=0 or bar_time<=0: raise ValueError("invalid quote or bar timestamp")
        ranges=[max(highs[i]-lows[i],abs(highs[i]-closes[i-1]),abs(lows[i]-closes[i-1])) for i in range(1,len(bars))]
        atr=sum(ranges[-14:])/min(14,len(ranges))
        if not math.isfinite(atr) or atr<=0: raise ValueError("invalid ATR")
        fast=_ema(closes[-80:],20); slow=_ema(closes[-150:],50)
        direction="BUY" if fast>slow else "SELL" if fast<slow else "HOLD"
        setup=min(1.0,abs(fast-slow)/(atr*.8)); quality=min(1.0,abs(closes[-1]-closes[-5])/(atr*1.5))
        spread=(ask-bid)/point
        # Feed the real completed candle series and live quote to SignalFlow.
        # Presence of "bars" selects the structural path; the legacy trend/setup
        # fields remain only for backward compatibility with other callers.
        market={"symbol":symbol,"timeframe":"M15","bars":bars,"latest_bar_time":bar_time,
          "bid":bid,"ask":ask,"point":point,"trend":direction,"setup_score":setup,
          "entry_quality":quality,"spread_points":spread,
          "account_balance":_num(p.get("account_balance",0),"account_balance"),
          "risk_percent":_num(p.get("risk_percent",0),"risk_percent"),
          "stop_loss_points":atr*1.5/point,
          "point_value_per_lot":_num(p.get("point_value_per_lot",0),"point_value_per_lot"),
          "volume_step":_num(p.get("volume_step",0),"volume_step"),
          "min_volume":_num(p.get("min_volume",0),"min_volume"),
          "max_volume":_num(p.get("max_volume",0),"max_volume")}
        result=SignalFlowPipeline().run(market)
        action=result["action"] if result["approved"] else "WAIT"
        stages=result["stages"]
        rejected_stage=result.get("rejected_by")
        reason=result["reason"] if result["approved"] else next(
          (stages[name].get("reason","SIGNALFLOW_REJECTED")
           for name in ("opportunities","entry_timing","risk_management")
           if not stages[name].get("approved",False)),
          "SIGNALFLOW_REJECTED")
        # One durable record per completed candle, but refresh it on subsequent ticks:
        # quote-aware timing must not be frozen by the first request on that candle.
        key=hashlib.sha256(f"{symbol}:M15:{bar_time}:django-signalflow-v2".encode()).hexdigest()[:16]
        opportunity_score=stages["opportunities"].get("score",setup)
        confidence=max(0.0,min(1.0,_num(opportunity_score,"opportunity score")))
        median=closes[-1]+(atr*.1 if action=="BUY" else -atr*.1 if action=="SELL" else 0)
        record,created=StrategyDecisionRecord.objects.update_or_create(
          decision_id=key,
          defaults={"symbol":symbol,"action":action,"confidence":confidence,
            "rationale_code":reason,"model_version":"django-signalflow-structural-v2",
            "features":{"ema20":fast,"ema50":slow,"atr":atr,"setup_score":setup,
              "entry_quality":quality,"spread_points":spread,"approved":result["approved"],
              "rejected_by":rejected_stage,"signalflow_action":result["action"],
              "opportunity_score":confidence}})
        risk=stages["risk_management"]
        response=_compat(record.action,record.rationale_code,key,bar_time,bid,ask,atr,median,spread,confidence,
          bool(risk.get("approved")))
        response["decision_record_created"]=created
        response["stages"]=stages
        response["signalflow_action"]=result["action"]
        response["signalflow_approved"]=bool(result["approved"])
        response["signalflow_rejected_by"]=rejected_stage
        response["signalflow_contract_version"]=result.get("contract_version")
        response["risk_volume"]=risk.get("volume",0.0)
        response["stop_loss_price"]=risk.get("stop_price")
        response["stop_loss_points"]=risk.get("stop_points")
        response["stop_distance"]=risk.get("stop_points",0.0)*point if risk.get("stop_points") else atr*1.5
        return JsonResponse(response)
    except (KeyError,TypeError,ValueError,OverflowError,json.JSONDecodeError) as exc:
        return JsonResponse({"ok":False,"error":str(exc)},status=400)
