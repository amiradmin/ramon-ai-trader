"""Paper-only hourly observer. Deliberately has no command/learning producer."""
from dataclasses import asdict
from datetime import datetime, timedelta, timezone as utc_timezone
import hashlib
import json
from pathlib import Path
from ramon_pipeline.signalflow import trend_shadow

from django.conf import settings
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET

from ramon_pipeline.signalflow.trend_shadow import VERSION, TrendPolicy, TrendCosts, detect_trend, resolve_trend, aggregate_h4
from .models import EAHeartbeat, MarketBar, TrendObservation, TrendWorkerState
from .scalp_observer import candle


def scope_feed():
    return EAHeartbeat.objects.filter(instance_id=settings.RAMON_TREND_INSTANCE,
        account_login=settings.RAMON_TREND_LOGIN,server_name=settings.RAMON_TREND_SERVER).first()


def health(reason, now):
    TrendWorkerState.objects.update_or_create(source=settings.RAMON_TREND_INSTANCE,
        defaults={'updated_at':now,'reason':reason})


def record_trend(feed, *, now=None):
    now=now or timezone.now()
    with transaction.atomic():
        feed=EAHeartbeat.objects.select_for_update().get(pk=feed.pk)
        p=feed.payload
        if (type(p.get('account_trade_mode')) is not int or p['account_trade_mode']!=0
                or p.get('server_name')!=feed.server_name or feed.status!='TELEMETRY_OK'
                or not feed.terminal_connected or not 0<=(now-feed.last_seen).total_seconds()<=60):
            health('DEMO_FEED_NOT_READY',now); return 0
        try:
            stamp=parse_datetime(str(p.get('quote_time','')))
            if not stamp or not timezone.is_aware(stamp) or not -5<=(now-stamp).total_seconds()<=30:
                health('QUOTE_STALE',now); return 0
            bid,ask,point=(float(p['quote'][k]) for k in ('bid','ask','point'))
            costs=TrendCosts(point,(ask-bid)/point,settings.RAMON_TREND_SLIPPAGE_POINTS,
                settings.RAMON_TREND_COMMISSION_POINTS,settings.RAMON_TREND_OVERNIGHT_POINTS,
                settings.RAMON_TREND_ROLLOVER_UTC_HOUR)
            tick=float(p['symbol_info']['tick_size'])
        except (KeyError,TypeError,ValueError,ZeroDivisionError):
            health('INVALID_COST_OR_CONTRACT',now); return 0
        rows=list(MarketBar.objects.filter(source=feed.instance_id,symbol=feed.symbol,timeframe='H1',
            timestamp__lte=now-timedelta(hours=1),received_at__lte=now).order_by('-timestamp')[:1024])[::-1]
        if not rows or not 0<=(now-rows[-1].timestamp-timedelta(hours=1)).total_seconds()<3600:
            health('H1_STALE_OR_MISSING',now); return 0
        key=dict(source=feed.instance_id,account_login=feed.account_login,server_name=feed.server_name,
                 symbol=feed.symbol,version=VERSION,signal_bar_time=rows[-1].timestamp)
        if TrendObservation.objects.filter(**key).exists():
            health('COLLECTING',now); return 0
        if (now-rows[-1].timestamp-timedelta(hours=1)).total_seconds()>300:
            health('WAITING_NEXT_H1_EVALUATION_WINDOW',now); return 0
        inputs=[candle(r) for r in rows]
        plan=detect_trend(inputs,bid=bid,costs=costs,tick_size=tick)
        raw=dict(plan)
        if TrendObservation.objects.filter(source=feed.instance_id,account_login=feed.account_login,
                server_name=feed.server_name,symbol=feed.symbol,version=VERSION,status='WAITING').exists():
            plan={**plan,'action':'WAIT','reason':'PAPER_POSITION_UNSETTLED'}
        start=datetime.fromtimestamp((int(now.timestamp())//3600+1)*3600,utc_timezone.utc)
        TrendObservation.objects.create(**key,recorded_at=now,evaluation_start=start,
            evaluation_end=start+timedelta(hours=48),action=plan['action'],reason=plan['reason'],
            status='WAITING' if plan['action'] in {'BUY','SELL'} else 'NOT_ENTERED',
            snapshot={'plan':plan,'raw_signal':raw,'costs':asdict(costs),'policy':asdict(TrendPolicy()),
                'inputs':inputs,'input_received_at':[r.received_at.isoformat() for r in rows],
                'input_sha256':hashlib.sha256(json.dumps(inputs,sort_keys=True).encode()).hexdigest(),
                'policy_sha256':hashlib.sha256(Path(trend_shadow.__file__).read_bytes()).hexdigest(),
                'h4_count':len(aggregate_h4(inputs)),'tick_size':tick,
                'h1_gap_count':sum(b['time']-a['time']!=3600 for a,b in zip(inputs,inputs[1:])),
                'execution':'NEXT_FULL_H1_OPEN_AFTER_RECORDING; PAPER_ONLY',
                'funding_note':'Assumed debit both sides, UTC rollover, Wednesday triple; not broker swap'},outcome={})
        health(plan['reason'],now); return 1


def settle_trends(*,now=None):
    now=now or timezone.now(); count=0
    ids=list(TrendObservation.objects.filter(status='WAITING',evaluation_start__lte=now-timedelta(hours=1))
             .values_list('pk',flat=True)[:100])
    for pk in ids:
        with transaction.atomic():
            row=TrendObservation.objects.select_for_update().get(pk=pk)
            if row.status!='WAITING': continue
            state=dict(row.outcome); bars=list(state.get('observed_bars',[]))
            if row.recorded_at>=row.evaluation_start:
                row.status='SKIPPED'; state['result']='NOT_FORWARD_RECORDED'
            else:
                while len(bars)<48:
                    expected=row.evaluation_start+timedelta(hours=len(bars))
                    if expected+timedelta(hours=1)>now: break
                    available=list(MarketBar.objects.filter(source=row.source,symbol=row.symbol,timeframe='H1',
                        timestamp__gte=expected,timestamp__lt=expected+timedelta(hours=1),received_at__lte=now))
                    if len(available)!=1 or available[0].timestamp!=expected:
                        if now>=expected+timedelta(hours=2):
                            row.status='SKIPPED'; state['result']='MISSING_OR_OFF_GRID_OUTCOME'
                        break
                    bars.append({**candle(available[0]),'received_at':available[0].received_at.isoformat(),
                                 'frozen_at':now.isoformat()})
                    result=resolve_trend(bars,row.snapshot['plan'],TrendCosts(**row.snapshot['costs']))
                    state.update(result)
                    if result['result']!='OPEN':
                        row.status='SCORED' if result['result'] in {'STOP','TARGET','TIME'} else 'SKIPPED'
                        break
            state['observed_bars']=bars; row.outcome=state
            if row.status in {'SCORED','SKIPPED'}: row.settled_at=now; count+=1
            row.save(update_fields=['status','outcome','settled_at'])
    return count


def run_trend_cycle():
    if not settings.RAMON_TREND_SHADOW_ENABLED: return 0,0
    settled=settle_trends()
    feed=scope_feed()
    if not feed:
        health('CONFIGURED_FEED_MISSING',timezone.now()); return 0,settled
    return record_trend(feed),settled


@require_GET
def trend_status(request):
    feed=scope_feed(); state=TrendWorkerState.objects.filter(source=settings.RAMON_TREND_INSTANCE).first()
    now=timezone.now()
    result=dict(mode='FORWARD_PAPER_ONLY',execution_enabled=False,version=VERSION,policy=asdict(TrendPolicy()),
        worker_reason=state.reason if state else 'NOT_STARTED',
        worker_fresh=bool(state and 0<=(now-state.updated_at).total_seconds()<=90),
        worker_at=state.updated_at if state else None,recent=[],scored=0,pending=0,skipped=0,net_points=0,
        mean_net_r=None,win_rate=None,no_trade_baseline_net_points=0,
        costs_note=f'Quote spread; assumed {settings.RAMON_TREND_SLIPPAGE_POINTS:g}-point slippage each side, {settings.RAMON_TREND_COMMISSION_POINTS:g}-point commission, {settings.RAMON_TREND_OVERNIGHT_POINTS:g}-point nightly debit; Wednesday triple. No broker P&L.',
        comparison_note='No like-for-like main/scalper comparison established; different horizons and samples.')
    if feed:
        rows=list(TrendObservation.objects.filter(source=feed.instance_id,account_login=feed.account_login,
            server_name=feed.server_name,symbol=feed.symbol,version=VERSION).order_by('-recorded_at')[:2000])
        scored=sorted((r for r in rows if r.status=='SCORED'),key=lambda r:r.settled_at)
        cumulative=peak=drawdown=0.0
        for r in scored:
            cumulative+=r.outcome['net_r']; peak=max(peak,cumulative); drawdown=max(drawdown,peak-cumulative)
        result.update(source=feed.instance_id,symbol=feed.symbol,recorded=len(rows),sample_limit=2000,
            scored=len(scored),pending=sum(r.status=='WAITING' for r in rows),
            skipped=sum(r.status=='SKIPPED' for r in rows),net_points=sum(r.outcome['net_points'] for r in scored),
            mean_net_r=cumulative/len(scored) if scored else None,
            win_rate=sum(r.outcome['net_points']>0 for r in scored)/len(scored) if scored else None,
            closed_cumulative_r_drawdown=drawdown,
            recent=[dict(recorded_at=r.recorded_at,action=r.action,reason=r.reason,status=r.status,
                result=r.outcome.get('result'),net_points=r.outcome.get('net_points')) for r in rows[:10]])
    response=JsonResponse(result,json_dumps_params={'allow_nan':False}); response['Cache-Control']='no-store'
    return response
