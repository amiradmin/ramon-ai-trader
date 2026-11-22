"""Isolated, bounded historical SignalFlow strategy research.

No EA/command functions are imported. Assumed spreads and contract economics are
explicit inputs, not broker-verified historical fills.
"""
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import math

from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django.test.utils import override_settings

from .models import MarketBar
from .strategy_registry import STRATEGIES
from .decision import _build_pipeline, _ema

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
    if volume_max < volume_min:
        raise ValueError("Maximum lot must be >= minimum lot")
    return dict(symbol=symbol,source=source,start=start,end=end,balance=balance,
       spread_points=spread_points,point=point,tick_size=tick_size,tick_value=tick_value,
       volume_min=volume_min,volume_max=volume_max,volume_step=volume_step)

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

def _simulate(data, rows):
    spread = data["spread_points"]*data["point"]
    value_per_price=data["tick_value"]/data["tick_size"]
    setups=list(STRATEGIES)
    # Research pipelines use exactly the worker's detector and risk implementation,
    # but never the live decision API or its persistent command boundary.
    with override_settings(RAMON_DEDUPE_SIGNALS=False):
        pipelines={setup:_build_pipeline(demo_mode=False,strategy_setup=setup) for setup in setups}
    book={name:{"open":None,"trades":[],"equity":data["balance"],"peak":data["balance"],"drawdown":0,"signals":0}
          for name in setups}
    first_ts=int(datetime.combine(data["start"],time.min,tzinfo=timezone.utc).timestamp())
    end_ts=int(datetime.combine(data["end"]+timedelta(days=1),time.min,tzinfo=timezone.utc).timestamp())
    for idx in range(WARMUP,len(rows)):
        bar=rows[idx]
        previous=rows[idx-1]
        within=first_ts<=bar["time"]<end_ts
        # Fill pending orders at next candle OPEN (never at a signal candle close).
        for name,state in book.items():
            pos=state.get("pending")
            if pos is not None:
                state["pending"]=None
                if within and state["open"] is None:
                    entry=bar["open"]+(spread if pos["side"]=="BUY" else 0)
                    if (pos["side"]=="BUY" and pos["stop"]<entry<pos["target"]) or (pos["side"]=="SELL" and pos["target"]<entry<pos["stop"]):
                        pos["entry"]=entry;pos["opened_at"]=bar["time"]
                        state["open"]=pos
            pos=state["open"]
            if pos is None: continue
            # Bid-chart approximation; conservative SL first if both hit same bar.
            stop_hit=bar["low"]<=pos["stop"] if pos["side"]=="BUY" else bar["high"]+spread>=pos["stop"]
            target_hit=bar["high"]>=pos["target"] if pos["side"]=="BUY" else bar["low"]+spread<=pos["target"]
            if not (stop_hit or target_hit):continue
            exit_price=pos["stop"] if stop_hit else pos["target"]
            direction=1 if pos["side"]=="BUY" else -1
            pnl=(exit_price-pos["entry"])*direction*value_per_price*pos["volume"]
            state["equity"]+=pnl
            state["peak"]=max(state["peak"],state["equity"])
            if state["peak"]>0:state["drawdown"]=max(state["drawdown"],(state["peak"]-state["equity"])/state["peak"]*100)
            state["trades"].append({"setup":name,"side":pos["side"],"opened_at":pos["opened_at"],
               "closed_at":bar["time"],"entry":round(pos["entry"],5),"exit":round(exit_price,5),
               "volume":pos["volume"],"pnl":round(pnl,2),"reason":"STOP" if stop_hit else "TARGET"})
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
            pipeline=pipelines[name]
            market=dict(symbol=data["symbol"],timeframe="M15",bars=[
                dict(time=b["time"],open=b["open"],high=b["high"],low=b["low"],close=b["close"],closed=True)
                for b in history],last_bar_closed=True,latest_bar_time=bar["time"],
                bid=close,ask=close+spread,point=data["point"],
                trend="BUY" if fast>slow else "SELL" if fast<slow else "HOLD",
                setup_score=min(1,abs(fast-slow)/(atr*.8)),
                entry_quality=min(1,abs(close-history[-5]["close"])/(atr*1.5)),
                spread_points=data["spread_points"],
                account_balance=state["equity"],risk_percent=0.5,
                stop_loss_points=atr*1.5/data["point"],
                point_value_per_lot=value_per_price*data["point"],
                volume_step=data["volume_step"],min_volume=data["volume_min"],
                max_volume=data["volume_max"],stop_level_points=0,
                take_profit_points=max(1,2*atr/data["point"]),
                target_reward_risk=pipeline.risk_manager.config.target_reward_risk,
                open_risk_percent=0)
            try:
                result=pipeline.run(market)
            except (ValueError, TypeError, KeyError, ArithmeticError):
                continue
            if not result.get("approved") or result.get("action") not in ("BUY","SELL"):continue
            risk=result["stages"]["risk_management"]
            stop=risk.get("stop_price");target=risk.get("target_price")
            if target is None:
                target=risk.get("take_profit_price")
            volume=risk.get("volume")
            try:
                stop=float(stop);target=float(target);volume=float(volume)
                if not all(math.isfinite(v) for v in (stop,target,volume)) or volume<=0:continue
            except (ValueError,TypeError):continue
            state["signals"]+=1
            state["pending"]=dict(side=result["action"],stop=stop,target=target,volume=volume)
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
           open_position=state["open"] is not None,trades=trades))
    return report

@require_GET
def backtest(request):
    try:
        data=_inputs(request)
        start_dt=datetime.combine(data["start"],time.min,tzinfo=timezone.utc)
        end_dt=datetime.combine(data["end"]+timedelta(days=1),time.min,tzinfo=timezone.utc)
        scope=dict(source=data["source"],symbol=data["symbol"],timeframe="M15")
        selected=MarketBar.objects.filter(**scope,timestamp__gte=start_dt,timestamp__lt=end_dt).order_by("timestamp")
        count=selected.count()
        if count+256>MAX_TEST_CANDLES:
            return JsonResponse({"error":f"Selected range contains {count} M15 candles; this interactive research runner allows at most {MAX_TEST_CANDLES-256} plus warmup. Choose a shorter range. Multi-year backtests need a background job."},status=422)
        prior=list(MarketBar.objects.filter(**scope,timestamp__lt=start_dt).order_by("-timestamp")[:256])
        prior.reverse()
        selected_rows=list(selected)
        all_rows=prior+selected_rows
        rows=[dict(time=int(r.timestamp.timestamp()),open=float(r.open),high=float(r.high),
              low=float(r.low),close=float(r.close)) for r in all_rows]
        if not selected_rows or len(rows)<WARMUP+2:
            return JsonResponse({"error":"Insufficient historical M15 data: need at least 130 candles including warmup."},status=422)
        report=_simulate(data,rows)
        return JsonResponse(dict(mode="ISOLATED_STRATEGY_RESEARCH_ESTIMATE",
             timeframe="M15",symbol=data["symbol"],source=data["source"],
             start=data["start"].isoformat(),end=data["end"].isoformat(),
             first_bar=datetime.fromtimestamp(rows[0]["time"],tz=timezone.utc).isoformat(),
             last_bar=datetime.fromtimestamp(rows[-1]["time"],tz=timezone.utc).isoformat(),
             candles=len(rows),assumptions={k:data[k] for k in (
               "balance","spread_points","point","tick_size","tick_value","volume_min","volume_max","volume_step")},
             limitations="Model-priced fixed spread, no historical spread/slippage/fees; OHLC intrabar order assumed stop first; no news/account state history; not broker validation, not full live-worker parity.",
             strategies=report))
    except ValueError as error:
        return JsonResponse({"error":str(error)},status=400)
