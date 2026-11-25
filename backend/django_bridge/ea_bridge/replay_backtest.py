"""Isolated, bounded historical SignalFlow strategy research.

No EA/command functions are imported. Assumed spreads and contract economics are
explicit inputs, not broker-verified historical fills.
"""
from datetime import date, datetime, time, timedelta, timezone
from dataclasses import replace
from decimal import Decimal
import math
import hashlib

from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django.test.utils import override_settings

from .models import MarketBar
from .strategy_registry import STRATEGIES
from .decision import _build_pipeline, _ema
from ramon_pipeline.signalflow.scalping import ScalpCosts, replay_scalper, valid_bar

MAX_TEST_CANDLES = 1500
WARMUP = 128

def _positive(raw, name, low=0.0, high=1e9):
    try:
        value = float(raw)
        if not math.isfinite(value) or not low < value <= high:
            raise ValueError()
        return value
    except (ValueError, TypeError):
        raise ValueError("Invalid or missing "+name)

def _nonnegative(raw, name, high=1e6):
    try:
        value = float(raw)
        if not math.isfinite(value) or not 0 <= value <= high:
            raise ValueError()
        return value
    except (ValueError, TypeError):
        raise ValueError("Invalid or missing "+name)

def _inputs(request):
    symbol = str(request.GET.get("symbol", "")).strip()
    source = str(request.GET.get("source", "")).strip()
    if not symbol or len(symbol)>32 or not source or len(source)>128:
        raise ValueError("Select a valid symbol and the exact market-data source")
    try:
        start = date.fromisoformat(request.GET["start"])
        end = date.fromisoformat(request.GET["end"])
    except (ValueError, KeyError):
        raise ValueError("Start and end dates must be YYYY-MM-DD")
    if start>end or end>date.today():
        raise ValueError("Invalid date range")
    balance = _positive(request.GET.get("balance"),"starting balance",0,1e9)
    spread_points = _positive(request.GET.get("spread_points"),"assumed spread (points)",0,1e5)
    point = _positive(request.GET.get("point"),"symbol point",0,1e3)
    tick_size = _positive(request.GET.get("tick_size"),"tick size",0,1e3)
    tick_value = _positive(request.GET.get("tick_value"),"tick value per lot",0,1e6)
    volume_min = _positive(request.GET.get("volume_min"),"minimum lot",0,1e3)
    volume_step = _positive(request.GET.get("volume_step"),"lot step",0,1e3)
    volume_max = _positive(request.GET.get("volume_max"),"maximum lot",0,1e6)
    risk_percent = _positive(request.GET.get("risk_percent", "0.5"), "risk per trade (%)", 0, 0.5)
    stop_buffer_atr = _nonnegative(request.GET.get("stop_buffer_atr", "0.25"), "structural stop buffer (ATR)", 1.5)
    target_reward_risk = _positive(request.GET.get("target_reward_risk", "1.25"), "target reward/risk", 1.2 - 1e-9, 5)
    minimum_opportunity_score = _nonnegative(request.GET.get("minimum_opportunity_score", "0.55"), "minimum opportunity score", 1)
    minimum_entry_quality = _nonnegative(request.GET.get("minimum_entry_quality", "0.55"), "minimum entry quality", 1)
    slippage_points = _nonnegative(request.GET.get("slippage_points", "0"), "slippage (points per side)", 1e4)
    commission_per_lot = _nonnegative(request.GET.get("commission_per_lot", "0"), "round-trip commission per lot", 1e6)
    if volume_max < volume_min:
        raise ValueError("Maximum lot must be >= minimum lot")
    values = (request.GET.getlist("setups") if hasattr(request.GET, "getlist") else request.GET.get("setups")) if "setups" in request.GET else None
    if values is None:
        setups = list(STRATEGIES)  # Existing API calls and queued jobs keep their prior behavior.
    else:
        if isinstance(values, str):
            values = [values]
        if not values or any(value not in (*STRATEGIES, "FAST_SCALP") for value in values) or len(values) != len(set(values)):
            raise ValueError("Select at least one valid, unique strategy")
        if "FAST_SCALP" in values and len(values) != 1:
            raise ValueError("Fast Scalp requires a separate M1/M5 test; select it alone")
        setups = [setup for setup in (*STRATEGIES, "FAST_SCALP") if setup in values]
    return dict(symbol=symbol,source=source,start=start,end=end,balance=balance,
       spread_points=spread_points,point=point,tick_size=tick_size,tick_value=tick_value,
       volume_min=volume_min,volume_max=volume_max,volume_step=volume_step,setups=setups,
       risk_percent=risk_percent,stop_buffer_atr=stop_buffer_atr,target_reward_risk=target_reward_risk,
       minimum_opportunity_score=minimum_opportunity_score,minimum_entry_quality=minimum_entry_quality,
       slippage_points=slippage_points,commission_per_lot=commission_per_lot)

def _indicators(history):
    closes=[b["close"] for b in history]
    true_ranges=[
       max(history[i]["high"]-history[i]["low"],abs(history[i]["high"]-closes[i-1]),
           abs(history[i]["low"]-closes[i-1])) for i in range(1,len(history))
    ]
    atr=sum(true_ranges[-14:])/14
    fast=_ema(closes[-80:],20)
    slow=_ema(closes[-150:],50)
    return atr,fast,slow

def _clean_m15_rows(rows):
    """Keep original on-grid bars only: never shift timestamps or invent candles."""
    clean=[]
    offsets={}
    excluded={"off_grid":0,"duplicate_slot":0,"short_interval":0}
    examples=[]
    for row in rows:
        ts=row["time"]
        offset=ts % 900
        offsets[str(offset)]=offsets.get(str(offset),0)+1
        if offset != 0:
            excluded["off_grid"]+=1
            if len(examples)<5:examples.append({"reason":"OFF_GRID","time":ts})
            continue
        if clean and ts==clean[-1]["time"]:
            excluded["duplicate_slot"]+=1
            if len(examples)<5:examples.append({"reason":"DUPLICATE_SLOT","time":ts})
            continue
        if clean and ts-clean[-1]["time"]<900:
            excluded["short_interval"]+=1
            if len(examples)<5:examples.append({"reason":"SHORT_INTERVAL","time":ts})
            continue
        clean.append(row)
    gaps=sum(b["time"]-a["time"]>900 for a,b in zip(clean,clean[1:]))
    digest=hashlib.sha256()
    for row in clean:
        digest.update(("{time}:{open:.8f}:{high:.8f}:{low:.8f}:{close:.8f};".format(
            time=row["time"],open=float(row.get("open",0)),
            high=float(row.get("high",0)),low=float(row.get("low",0)),
            close=float(row["close"]))).encode())
    return clean,{"input_candles":len(rows),"accepted_candles":len(clean),
                  "offset_counts":offsets,"first_raw":rows[0]["time"] if rows else None,
                  "last_raw":rows[-1]["time"] if rows else None,
                  "history_fingerprint":digest.hexdigest(),
                  "excluded":excluded,"examples":examples,"gap_count":gaps,
                  "method":"EXCLUDE_OFF_GRID_OR_DUPLICATE_ONLY_NO_SYNTHETIC_CANDLES"}

def _research_pipeline(setup, *, risk_percent=0.5, stop_buffer_atr=None, target_reward_risk=None,
                       minimum_opportunity_score=None, minimum_entry_quality=None, opportunity_overrides=None,
                       isolated_risk_sensitivity=False):
    """Use standard risk policy, enabling exactly one config-approved research setup."""
    pipeline=_build_pipeline(demo_mode=False,strategy_setup=setup)
    configs=pipeline.opportunities.trading_settings["opportunity_finder"]["setups"]
    name=setup.lower()
    if name not in configs:
        raise ValueError("Unknown strategy setup config: "+name)
    policy=configs[name]
    if not (policy.get("enabled") or policy.get("demo_enabled")):
        raise ValueError("Strategy disabled in both standard and research configuration: "+name)
    for key,settings in configs.items():
        settings["enabled"]=(key==name)
    # Tune only the isolated research pipeline, never persisted/live thresholds.
    if opportunity_overrides:
        allowed = {"min_impulse_atr", "min_retracement", "max_retracement", "zone_tolerance_atr", "regime_lookback"}
        if setup != "TREND_PULLBACK" or not set(opportunity_overrides) <= allowed:
            raise ValueError("Unsupported research opportunity overrides")
        pipeline.opportunities.config = replace(pipeline.opportunities.config, **opportunity_overrides)
    if minimum_opportunity_score is not None:
        pipeline.opportunities.minimum_score = minimum_opportunity_score
    if minimum_entry_quality is not None:
        pipeline.entry_timer.minimum_quality = minimum_entry_quality
    if isolated_risk_sensitivity:
        if setup != "TREND_PULLBACK" or isinstance(risk_percent, bool) or not 0 < risk_percent <= 1.5:
            raise ValueError("Invalid isolated Trend Pullback risk sensitivity")
        pipeline.risk_manager.maximum_risk_percent = min(
            risk_percent, pipeline.risk_manager.config.max_total_risk_percent
        )
    else:
        pipeline.risk_manager.maximum_risk_percent = min(
            pipeline.risk_manager.maximum_risk_percent, risk_percent, 0.5
        )
    updates = {}
    if stop_buffer_atr is not None:
        updates["stop_buffer_atr"] = stop_buffer_atr
    if target_reward_risk is not None:
        if target_reward_risk < pipeline.risk_manager.config.min_reward_risk:
            raise ValueError("Target reward/risk is below the configured minimum for "+setup)
        updates["target_reward_risk"] = target_reward_risk
    if updates:
        pipeline.risk_manager.config = replace(pipeline.risk_manager.config, **updates)
    return pipeline

def _trade_diagnostics(trades):
    """Descriptive slices of hypothetical closed trades, never entry filters."""
    def group(label):
        groups={}
        for trade in trades:
            key=label(trade)
            groups.setdefault(key,[]).append(trade)
        return [
            dict(label=key,trades=len(items),wins=sum(t["pnl"]>0 for t in items),
                net_pnl=round(sum(t["pnl"] for t in items),2),
                gross_profit=round(sum(t["pnl"] for t in items if t["pnl"]>0),2),
                gross_loss=round(sum(t["pnl"] for t in items if t["pnl"]<0),2))
            for key,items in sorted(groups.items())
        ]
    def month(trade):
        return datetime.fromtimestamp(trade["opened_at"],tz=timezone.utc).strftime("%Y-%m")
    def utc_block(trade):
        hour=datetime.fromtimestamp(trade["opened_at"],tz=timezone.utc).hour
        start=hour//6*6
        return f"{start:02d}:00–{start+5:02d}:59 UTC"
    return dict(
        by_month=group(month),by_side=group(lambda t:t["side"]),
        by_regime=group(lambda t:t.get("signal_regime") or "UNAVAILABLE"),
        by_utc_block=group(utc_block),by_exit=group(lambda t:t["reason"]),
        gap_affected_trades=sum(t.get("gaps_crossed",0)>0 for t in trades),
        gross_pnl=round(sum(t.get("gross_pnl",t["pnl"]) for t in trades),2),
        commission=round(sum(t.get("commission",0) for t in trades),2),
    )


def _simulate(data, rows, progress=None):
    spread = data["spread_points"]*data["point"]
    value_per_price=data["tick_value"]/data["tick_size"]
    risk_percent=data.get("risk_percent", 0.5)
    slippage=data.get("slippage_points", 0)*data["point"]
    commission_per_lot=data.get("commission_per_lot", 0)
    setups=data.get("setups", list(STRATEGIES))
    # Research pipelines use exactly the worker's detector and risk implementation,
    # but never the live decision API or its persistent command boundary.
    with override_settings(RAMON_DEDUPE_SIGNALS=False):
        pipelines={setup:_research_pipeline(
            setup, risk_percent=risk_percent,
            stop_buffer_atr=data.get("stop_buffer_atr"),
            target_reward_risk=data.get("target_reward_risk"),
            minimum_opportunity_score=data.get("minimum_opportunity_score"),
            minimum_entry_quality=data.get("minimum_entry_quality"),
            opportunity_overrides=data.get("opportunity_overrides"),
            isolated_risk_sensitivity=data.get("isolated_risk_sensitivity", False),
        ) for setup in setups}
    research = data.get("trend_research", {})
    if research and setups != ["TREND_PULLBACK"]:
        raise ValueError("Trend research options require Trend Pullback alone")
    hours = research.get("entry_hours")
    h1_closes = []
    h1_group = []
    book={name:{"open":None,"trades":[],"equity":data["balance"],"peak":data["balance"],"drawdown":0,"signals":0,
                "evaluations":0,"rejections":{},"rejection_examples":{},"rejection_details":{}}
          for name in setups}
    def rejected(state, stage, reason, bar_time, detail=None):
        # Aggregate actual returned stage reasons, not guesses about why a trade
        # did not execute. Keep a bounded sample for inspection without huge jobs.
        code=str(reason or "UNSPECIFIED")[:128]
        key=stage+":"+code
        state["rejections"][key]=state["rejections"].get(key,0)+1
        examples=state["rejection_examples"].setdefault(key,[])
        if len(examples)<3:
            examples.append(bar_time)
        if detail and key not in state["rejection_details"]:
            state["rejection_details"][key]=str(detail)[:400]
    first_ts=int(datetime.combine(data["start"],time.min,tzinfo=timezone.utc).timestamp())
    end_ts=int(datetime.combine(data["end"]+timedelta(days=1),time.min,tzinfo=timezone.utc).timestamp())
    for idx in range(WARMUP,len(rows)):
        if progress is not None and idx % 50 == 0:
            progress(idx, len(rows))
        bar=rows[idx]
        previous=rows[idx-1]
        gap_before_bar=bar["time"]-previous["time"]>900
        within=first_ts<=bar["time"]<end_ts
        # Only complete, contiguous UTC H1 groups; never use a future M15 bar.
        if research.get("h1_confirmation"):
            h1_group.append(bar)
            if bar["time"] % 3600 == 2700:
                if [x["time"] for x in h1_group[-4:]] == [bar["time"]-2700+i*900 for i in range(4)]:
                    h1_closes.append(bar["close"])
                h1_group = []
        # Fill pending orders at next candle OPEN (never at a signal candle close).
        for name,state in book.items():
            filled_this_bar=False
            pos=state.get("pending")
            if pos is not None:
                state["pending"]=None
                if within and state["open"] is None and (not research or not gap_before_bar):
                    entry=bar["open"]+(spread+slippage if pos["side"]=="BUY" else -slippage)
                    if (pos["side"]=="BUY" and pos["stop"]<entry<pos["target"]) or (pos["side"]=="SELL" and pos["target"]<entry<pos["stop"]):
                        pos["entry"]=entry;pos["opened_at"]=bar["time"]
                        pos["initial_stop"]=pos["stop"]
                        pos["held_bars"]=0
                        state["open"]=pos
                        filled_this_bar=True
                        pos["gaps_crossed"]=int(gap_before_bar)
                    else:
                        rejected(state,"FILL","ENTRY_OUTSIDE_STOP_TARGET",bar["time"])
            pos=state["open"]
            if pos is None: continue
            if gap_before_bar and not filled_this_bar:
                pos["gaps_crossed"]=pos.get("gaps_crossed",0)+1
            # Bid-chart approximation; conservative SL first if both hit same bar.
            stop_hit=bar["low"]<=pos["stop"] if pos["side"]=="BUY" else bar["high"]+spread>=pos["stop"]
            target_hit=bar["high"]>=pos["target"] if pos["side"]=="BUY" else bar["low"]+spread<=pos["target"]
            pos["held_bars"] += 1
            timed = bool(research.get("time_exit") and pos["held_bars"] >= research["time_exit"])
            if not (stop_hit or target_hit or timed):
                # Stop changes take effect on the NEXT bar, not retrospectively.
                direction = 1 if pos["side"] == "BUY" else -1
                mark = bar["close"] if direction == 1 else bar["close"] + spread
                initial_r = abs(pos["entry"] - pos["initial_stop"])
                trigger = research.get("breakeven_r")
                proposed = pos["stop"]
                if trigger and direction * (mark-pos["entry"]) >= trigger * initial_r:
                    proposed = pos["entry"]
                trailing = research.get("trailing_atr")
                if trailing and direction * (mark-pos["entry"]) >= initial_r:
                    try:
                        trail_atr, _, _ = _indicators(rows[max(0,idx-255):idx+1])
                        if math.isfinite(trail_atr) and trail_atr > 0:
                            candidate = mark-direction*trailing*trail_atr
                            proposed = max(proposed,candidate) if direction == 1 else min(proposed,candidate)
                    except (ValueError, ZeroDivisionError):
                        pass
                pos["stop"] = max(pos["stop"],proposed) if direction == 1 else min(pos["stop"],proposed)
                continue
            level=pos["stop"] if stop_hit else pos["target"] if target_hit else (
                bar["close"] if pos["side"]=="BUY" else bar["close"]+spread)
            if research and stop_hit:
                opening_quote = bar["open"] if pos["side"]=="BUY" else bar["open"]+spread
                level = min(level,opening_quote) if pos["side"]=="BUY" else max(level,opening_quote)
            exit_price=level+(-slippage if pos["side"]=="BUY" else slippage)
            direction=1 if pos["side"]=="BUY" else -1
            gross_pnl=(exit_price-pos["entry"])*direction*value_per_price*pos["volume"]
            commission=commission_per_lot*pos["volume"]
            pnl=gross_pnl-commission
            state["equity"]+=pnl
            state["peak"]=max(state["peak"],state["equity"])
            if state["peak"]>0:state["drawdown"]=max(state["drawdown"],(state["peak"]-state["equity"])/state["peak"]*100)
            state["trades"].append({"setup":name,"side":pos["side"],"opened_at":pos["opened_at"],
               "closed_at":bar["time"],"entry":round(pos["entry"],5),"exit":round(exit_price,5),
               "volume":pos["volume"],"stop":round(pos["stop"],5),
               "target":round(pos["target"],5),"signal_at":pos["signal_at"],
               "pnl":round(pnl,2),"gross_pnl":round(gross_pnl,2),
               "commission":round(commission,2),"entry_slippage_points":data.get("slippage_points",0),
               "exit_slippage_points":data.get("slippage_points",0),
               "signal_regime":pos.get("signal_regime"),"signal_adx":pos.get("signal_adx"),
               "gaps_crossed":pos.get("gaps_crossed",0),
               "reason":"STOP" if stop_hit else "TARGET" if target_hit else "TIME_EXIT"})
            state["open"]=None
        # Don't evaluate the final candle: an order requires a next candle to fill.
        if idx==len(rows)-1 or not within:continue
        history=rows[max(0,idx-255):idx+1]
        if len(history)<WARMUP:continue
        try:atr,fast,slow=_indicators(history)
        except (ValueError,ZeroDivisionError):continue
        if not math.isfinite(atr) or atr<=0:continue
        close=bar["close"]
        for name,state in book.items():
            if state["open"] is not None or state.get("pending") is not None:continue
            if hours and not hours[0] <= ((bar["time"]+900)%86400)//3600 < hours[1]:
                rejected(state,"RESEARCH","OUTSIDE_ENTRY_WINDOW",bar["time"])
                continue
            pipeline=pipelines[name]
            market=dict(symbol=data["symbol"],timeframe="M15",bars=[
                dict(time=b["time"],open=b["open"],high=b["high"],low=b["low"],close=b["close"],closed=True)
                for b in history],last_bar_closed=True,latest_bar_time=bar["time"],
                bid=close,ask=close+spread,point=data["point"],
                trend="BUY" if fast>slow else "SELL" if fast<slow else "HOLD",
                setup_score=min(1,abs(fast-slow)/(atr*.8)),
                entry_quality=min(1,abs(close-history[-5]["close"])/(atr*1.5)),
                spread_points=data["spread_points"],
                account_balance=state["equity"],risk_percent=risk_percent,
                stop_loss_points=atr*1.5/data["point"],
                point_value_per_lot=value_per_price*data["point"],
                volume_step=data["volume_step"],min_volume=data["volume_min"],
                max_volume=data["volume_max"],stop_level_points=0,
                take_profit_points=max(1,2*atr/data["point"]),
                target_reward_risk=pipeline.risk_manager.config.target_reward_risk,
                open_risk_percent=0)
            state["evaluations"]+=1
            try:
                result=pipeline.run(market)
            except (ValueError, TypeError, KeyError, ArithmeticError) as exc:
                rejected(state,"EVALUATION",type(exc).__name__,bar["time"])
                continue
            if not result.get("approved") or result.get("action") not in ("BUY","SELL"):
                stages=result.get("stages") or {}
                rejection_stage=result.get("rejected_by")
                if rejection_stage not in ("opportunities","entry_timing","risk_management"):
                    rejection_stage=next((name for name in ("opportunities","entry_timing","risk_management")
                        if isinstance(stages.get(name),dict) and not stages[name].get("approved")),None)
                if rejection_stage:
                    rejected(state,rejection_stage.upper(),
                        (stages.get(rejection_stage) or {}).get("reason") or result.get("reason"),
                        bar["time"],
                        (stages.get(rejection_stage) or {}).get("explanation"))
                else:
                    rejected(state,"EVALUATION",result.get("reason") or "NO_APPROVED_SIGNAL",bar["time"])
                continue
            if research.get("h1_confirmation"):
                if len(h1_closes)<50:
                    rejected(state,"RESEARCH","H1_WARMUP",bar["time"])
                    continue
                hfast,hslow=_ema(h1_closes[-80:],20),_ema(h1_closes[-150:],50)
                if not ((result["action"]=="BUY" and hfast>hslow) or (result["action"]=="SELL" and hfast<hslow)):
                    rejected(state,"RESEARCH","H1_DIRECTION_MISMATCH",bar["time"])
                    continue
            risk=result["stages"]["risk_management"]
            stop=risk.get("stop_price");target=risk.get("target_price")
            if target is None:
                target=risk.get("take_profit_price")
            volume=risk.get("volume")
            try:
                stop=float(stop);target=float(target);volume=float(volume)
                if not all(math.isfinite(v) for v in (stop,target,volume)) or volume<=0:continue
            except (ValueError,TypeError):
                rejected(state,"RISK","INVALID_STOP_TARGET_OR_VOLUME",bar["time"])
                continue
            state["signals"]+=1
            regime=(result.get("stages") or {}).get("opportunities") or {}
            regime=regime.get("regime") or {}
            state["pending"]=dict(side=result["action"],stop=stop,target=target,volume=volume,
                signal_at=bar["time"],signal_regime=regime.get("label"),
                signal_adx=regime.get("adx"))
    if progress is not None:
        progress(len(rows), len(rows))
    report=[]
    for name,state in book.items():
        trades=state["trades"];wins=[t for t in trades if t["pnl"]>0]
        losses=[t for t in trades if t["pnl"]<0]
        gross_win=sum(t["pnl"] for t in wins);gross_loss=-sum(t["pnl"] for t in losses)
        report.append(dict(setup=name,signals=state["signals"],closed_trades=len(trades),
           wins=len(wins),losses=len(losses),
           win_rate=round(len(wins)/len(trades)*100,2) if trades else None,
           net_gain=round(state["equity"]-data["balance"],2),
           profit_factor=round(gross_win/gross_loss,2) if gross_loss else None,
           max_drawdown_pct=round(state["drawdown"],2),
           open_position=state["open"] is not None,trades=trades,
           trade_diagnostics=_trade_diagnostics(trades),
           evaluations=state["evaluations"],
           rejections=[dict(stage=key.split(":",1)[0],reason=key.split(":",1)[1],
                count=count,sample_bar_times=state["rejection_examples"][key],
                detail=state["rejection_details"].get(key))
              for key,count in sorted(state["rejections"].items(),key=lambda item:(-item[1],item[0]))[:25]],
           rejection_total=sum(state["rejections"].values())))
    return report

def _scalp_backtest_data(data, progress=None):
    """Separate M1/M5 research in points and R, without invented account sizing."""
    start=datetime.combine(data["start"],time.min,tzinfo=timezone.utc)
    end=datetime.combine(data["end"]+timedelta(days=1),time.min,tzinfo=timezone.utc)
    scope=dict(source=data["source"],symbol=data["symbol"])
    query=MarketBar.objects.filter(**scope,timeframe="M1",timestamp__gte=start,timestamp__lt=end).order_by("timestamp")
    count=query.count()
    if count>100000:
        return JsonResponse({"error":f"Fast Scalp has {count} M1 candles; select a shorter range (maximum 100,000)."},status=422)
    # 32 complete M5 bars need at least 160 M1 warm-up bars before the start date.
    prior=list(MarketBar.objects.filter(**scope,timeframe="M1",timestamp__lt=start).order_by("-timestamp")[:160])
    prior.reverse()
    raw=[dict(time=int(b.timestamp.timestamp()),open=float(b.open),high=float(b.high),
              low=float(b.low),close=float(b.close)) for b in prior+list(query)]
    m1=[bar for bar in raw if valid_bar(bar,60)]
    groups={}
    for bar in m1:
        groups.setdefault(bar["time"]//300*300,[]).append(bar)
    m5=[]
    for stamp,group in sorted(groups.items()):
        if [bar["time"] for bar in group] != [stamp+60*i for i in range(5)]:
            continue
        m5.append(dict(time=stamp,open=group[0]["open"],high=max(b["high"] for b in group),
                       low=min(b["low"] for b in group),close=group[-1]["close"]))
    history={"M1":m1,"M5":m5}
    quality={"M1":dict(input_candles=len(raw),accepted_candles=len(m1),excluded=len(raw)-len(m1),
                       gap_count=sum(b["time"]-a["time"]!=60 for a,b in zip(m1,m1[1:]))),
             "M5":dict(input_groups=len(groups),accepted_candles=len(m5),
                       incomplete_groups=len(groups)-len(m5),
                       gap_count=sum(b["time"]-a["time"]!=300 for a,b in zip(m5,m5[1:])),
                       method="COMPLETE_FIVE_CONSECUTIVE_M1_ONLY")}
    if len(history["M1"])<65 or len(history["M5"])<32 or not any(start.timestamp()<=b["time"]<end.timestamp() for b in history["M1"]):
        return JsonResponse({"error":"Fast Scalp needs completed, aligned M1 and M5 candles from this exact source and date range."},status=422)
    if progress:progress(0,1)
    value_per_point=data["tick_value"]*data["point"]/data["tick_size"]
    costs=ScalpCosts(data["point"],data["spread_points"],data.get("slippage_points",0),
                     data.get("commission_per_lot",0)/value_per_point)
    scalp=replay_scalper(history["M1"],history["M5"],costs=costs,tick_size=data["tick_size"],
                        score_start=int(start.timestamp()))
    if progress:progress(1,1)
    return JsonResponse(dict(mode="ISOLATED_SCALP_RESEARCH_ESTIMATE",timeframe="M1/M5",
        symbol=data["symbol"],source=data["source"],start=data["start"].isoformat(),
        end=data["end"].isoformat(),candles=len(history["M1"]),selected_setups=["FAST_SCALP"],
        strategies=[],scalp_research=scalp,scalp_data_quality=quality,
        assumptions={key:data.get(key) for key in ("spread_points","point","tick_size","tick_value",
                     "slippage_points","commission_per_lot")},
        limitations="Descriptive M1/M5 replay; complete M5 candles derived from stored M1. Fixed modeled costs, unknown historical broker stop level, no historical quotes or tick path. Points per lot and R are not account-currency profit or a 1,000/10,000 balance comparison."))

def run_backtest_data(data, *, max_candles=MAX_TEST_CANDLES, progress=None):
        if data.get("setups")==["FAST_SCALP"]:
            return _scalp_backtest_data(data,progress=progress)
        start_dt=datetime.combine(data["start"],time.min,tzinfo=timezone.utc)
        end_dt=datetime.combine(data["end"]+timedelta(days=1),time.min,tzinfo=timezone.utc)
        scope=dict(source=data["source"],symbol=data["symbol"],timeframe="M15")
        selected=MarketBar.objects.filter(**scope,timestamp__gte=start_dt,timestamp__lt=end_dt).order_by("timestamp")
        count=selected.count()
        if count+256>max_candles:
            return JsonResponse({"error":f"Selected range contains {count} M15 candles; this interactive research runner allows at most {max_candles-256} plus warmup. Choose a shorter range. For longer ranges, import complete historical data and use segmented batch processing."},status=422)
        prior=list(MarketBar.objects.filter(**scope,timestamp__lt=start_dt).order_by("-timestamp")[:256])
        prior.reverse()
        selected_rows=list(selected)
        all_rows=prior+selected_rows
        rows=[dict(time=int(r.timestamp.timestamp()),open=float(r.open),high=float(r.high),
              low=float(r.low),close=float(r.close)) for r in all_rows]
        if not selected_rows or len(rows)<WARMUP+2:
            return JsonResponse({"error":"Insufficient historical M15 data: need at least 130 candles including warmup."},status=422)
        rows, quality = _clean_m15_rows(rows)
        if len(rows)<WARMUP+2 or not any(start_dt.timestamp()<=row["time"]<end_dt.timestamp() for row in rows):
            return JsonResponse({"error":"Insufficient valid grid-aligned M15 history after excluding invalid candles.",
                "data_quality":quality},status=422)
        excluded_count=sum(quality["excluded"].values())
        excluded_fraction=excluded_count/max(1,quality["input_candles"])
        quality["excluded_fraction"]=round(excluded_fraction,5)
        quality["validation_status"]=(
            "DESCRIPTIVE_ONLY_HISTORY_QUALITY_WARNING"
            if excluded_fraction>0.05 or quality["gap_count"]>0
            else "HISTORY_FILTERED_CHECK_BROKER_FILLS"
        )
        report=_simulate(data,rows,progress=progress)
        return JsonResponse(dict(mode="ISOLATED_STRATEGY_RESEARCH_ESTIMATE",
             timeframe="M15",symbol=data["symbol"],source=data["source"],
             start=data["start"].isoformat(),end=data["end"].isoformat(),
             first_bar=datetime.fromtimestamp(rows[0]["time"],tz=timezone.utc).isoformat(),
             last_bar=datetime.fromtimestamp(rows[-1]["time"],tz=timezone.utc).isoformat(),
             candles=len(rows),data_quality=quality,
             research_profile=("ISOLATED_TREND_RISK_SENSITIVITY" if data.get("isolated_risk_sensitivity") else "STANDARD_RISK_EXPLICIT_SETUP_RESEARCH"),
             assumptions={**{k:data[k] for k in (
               "balance","spread_points","point","tick_size","tick_value","volume_min","volume_max","volume_step")},
               **{k:data.get(k,default) for k,default in (
                   ("risk_percent",0.5),("stop_buffer_atr",0.25),
                   ("target_reward_risk",1.25),("minimum_opportunity_score",0.55),
                   ("minimum_entry_quality",0.55),("slippage_points",0),("commission_per_lot",0))}},
             limitations="Model-priced fixed spread, per-side adverse slippage and round-trip commission assumptions; no historical cost records or gap fills; OHLC intrabar order assumed stop first; no news/account state history; not broker validation, not full live-worker parity.",
             selected_setups=data.get("setups",list(STRATEGIES)),strategies=report))


@require_GET
def backtest(request):
    try:
        return run_backtest_data(_inputs(request))
    except ValueError as error:
        return JsonResponse({"error": str(error)}, status=400)
