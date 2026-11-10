"""Forward paper scalping on completed M1/M5 data; cannot create commands."""
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timedelta, timezone as utc_timezone
import hashlib
import json

from django.conf import settings
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET

from ramon_pipeline.signalflow.scalping import (
    VERSION, ScalpConfig, ScalpCosts, detect_scalp, resolve_scalp,
)
from .models import EAHeartbeat, MarketBar, ScalpObservation, ScalperControl
from .scalper_audit import health_skip, provenance, policy_artifact


def candle(bar):
    return {"time": bar.timestamp.timestamp(), **{k: float(getattr(bar, k)) for k in ('open','high','low','close')}}


def record_scalp(feed, *, now=None):
    now = now or timezone.now()
    # Lock the same feed as telemetry, and re-read under lock before snapshotting.
    with transaction.atomic():
        feed = EAHeartbeat.objects.select_for_update().get(pk=feed.pk)
        p = feed.payload
        if (not isinstance(p, dict) or type(p.get('account_trade_mode')) is not int or p['account_trade_mode'] != 0
            or not feed.terminal_connected or not 0 <= (now-feed.last_seen).total_seconds() <= 30
            or p.get('server_name') != feed.server_name):
            health_skip(feed,'DEMO_FEED_NOT_READY',now)
            return 0
        try:
            quote_time = parse_datetime(str(p.get('quote_time', '')))
            if quote_time is None or not timezone.is_aware(quote_time) or not -5 <= (now-quote_time).total_seconds() <= 20:
                health_skip(feed,'QUOTE_STALE_OR_MISSING',now)
                return 0
            quote, info = p['quote'], p['symbol_info']
            point, bid, ask = (float(quote[k]) for k in ('point','bid','ask'))
            if bid <= 0 or ask < bid:
                health_skip(feed,'INVALID_QUOTE',now)
                return 0
            costs = ScalpCosts(point, (ask-bid)/point,
                float(getattr(settings, 'RAMON_SCALP_SLIPPAGE_POINTS', 5)),
                float(getattr(settings, 'RAMON_SCALP_COMMISSION_POINTS', 5)))
            tick_size, stop_level = float(info['tick_size']), float(info['stops_level_points'])
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            health_skip(feed,'INVALID_COST_OR_CONTRACT',now)
            return 0
        m1 = list(MarketBar.objects.filter(source=feed.instance_id, symbol=feed.symbol, timeframe='M1',
            received_at__lte=now, timestamp__lte=now-timedelta(minutes=1)).order_by('-timestamp')[:64])[::-1]
        if not m1 or not 0 <= (now-m1[-1].timestamp-timedelta(minutes=1)).total_seconds() <= 30:
            health_skip(feed,'M1_STALE_OR_MISSING',now)
            return 0
        key = dict(source=feed.instance_id, account_login=feed.account_login, server_name=feed.server_name,
                   symbol=feed.symbol, version=VERSION, signal_bar_time=m1[-1].timestamp)
        if ScalpObservation.objects.filter(**key).exists():
            return 0
        cutoff = m1[-1].timestamp+timedelta(minutes=1)
        m5 = list(MarketBar.objects.filter(source=feed.instance_id, symbol=feed.symbol, timeframe='M5',
            timestamp__lte=cutoff-timedelta(minutes=5), received_at__lte=now).order_by('-timestamp')[:32])[::-1]
        inputs = {'M1': [candle(b) for b in m1], 'M5': [candle(b) for b in m5]}
        plan = detect_scalp(inputs['M1'], inputs['M5'], bid=bid, costs=costs, tick_size=tick_size,
                            stop_level_points=stop_level)
        raw_plan = dict(plan)
        active = ScalpObservation.objects.filter(source=feed.instance_id, account_login=feed.account_login,
            server_name=feed.server_name, symbol=feed.symbol, version=VERSION, status='WAITING').exists()
        if active:
            plan = {**plan, 'action': 'WAIT', 'reason': 'PAPER_POSITION_UNSETTLED'}
        start = datetime.fromtimestamp((int(now.timestamp())//60+1)*60, utc_timezone.utc)
        artifact = policy_artifact(feed, now)
        ScalpObservation.objects.create(**key, action=plan['action'], reason=plan['reason'],
            recorded_at=now, evaluation_start=start, evaluation_end=start+timedelta(minutes=5),
            status='WAITING' if plan['action'] in {'BUY','SELL'} else 'NOT_ENTERED',
            snapshot={'plan': plan, 'raw_signal': raw_plan, 'costs': asdict(costs), 'policy': asdict(ScalpConfig()),
                'provenance': provenance(), 'policy_artifact_key': artifact.event_key, 'quote_time': p.get('quote_time'), 'heartbeat_at': feed.last_seen.isoformat(),
                'input_received_at': {'M1':[b.received_at.isoformat() for b in m1],
                                      'M5':[b.received_at.isoformat() for b in m5]},
                'bid': bid, 'ask': ask, 'tick_size': tick_size, 'stop_level_points': stop_level,
                'input_sha256': hashlib.sha256(json.dumps(inputs,sort_keys=True).encode()).hexdigest(),
                'inputs': inputs, 'cost_note': 'Frozen quote spread; assumed slippage each side and round-trip commission',
                'execution': 'NEXT_FULL_M1_OPEN_NOT_A_BROKER_FILL'}, outcome={})
        return 1


def settle_scalps(*, now=None):
    now = now or timezone.now()
    ids = list(ScalpObservation.objects.filter(status='WAITING', evaluation_start__lte=now-timedelta(minutes=1))
               .order_by('evaluation_start').values_list('pk', flat=True)[:200])
    count = 0
    for pk in ids:
        with transaction.atomic():
            row = ScalpObservation.objects.select_for_update().get(pk=pk)
            if row.status != 'WAITING':
                continue
            state = dict(row.outcome)
            bars = list(state.get('observed_bars', []))
            if row.recorded_at >= row.evaluation_start:
                row.status = 'SKIPPED'; state['result'] = 'NOT_FORWARD_RECORDED'
            else:
                while len(bars) < 5:
                    expected = row.evaluation_start+len(bars)*timedelta(minutes=1)
                    if expected+timedelta(minutes=1) > now:
                        break
                    available = list(MarketBar.objects.filter(source=row.source, symbol=row.symbol, timeframe='M1',
                        received_at__lte=now, timestamp__gte=expected, timestamp__lt=expected+timedelta(minutes=1)))
                    if len(available) != 1 or available[0].timestamp != expected:
                        if now >= expected+timedelta(minutes=11):
                            row.status = 'SKIPPED'; state['result'] = 'MISSING_OR_OFF_GRID_OUTCOME'
                        break
                    bars.append({**candle(available[0]), 'received_at': available[0].received_at.isoformat(),
                                 'frozen_at': now.isoformat()})
                    result = resolve_scalp(bars, row.snapshot['plan'], ScalpCosts(**row.snapshot['costs']))
                    state.update(result)
                    if result['result'] != 'OPEN':
                        row.status = 'SCORED' if result['result'] in {'STOP','TARGET','TIME'} else 'SKIPPED'
                        break
            state['observed_bars'] = bars
            row.outcome = state
            if row.status in {'SCORED','SKIPPED'}:
                row.settled_at = now; count += 1
            row.save(update_fields=['status','outcome','settled_at'])
    return count


def run_scalp_cycle():
    if not getattr(settings, 'RAMON_SCALP_SHADOW_ENABLED', False):
        return 0, 0
    settled = settle_scalps()
    recorded = sum(record_scalp(feed) for feed in EAHeartbeat.objects.filter(status='TELEMETRY_OK').order_by('-last_seen')[:20])
    return recorded, settled


@require_GET
def scalp_status(request):
    feed = EAHeartbeat.objects.filter(status='TELEMETRY_OK').order_by('-last_seen').first()
    result = dict(mode='FORWARD_PAPER_ONLY', execution_enabled=False, version=VERSION,
        enabled=getattr(settings, 'RAMON_SCALP_SHADOW_ENABLED', False), recent=[], scored=0,
        policy=asdict(ScalpConfig()), costs_note='Quote spread plus assumed 5-point slippage per side and 5-point round-trip commission. No broker P&L.')
    result.update(broker_readiness='NO_FEED',automatic_entries=False,broker_execution_enabled=False)
    if feed:
        from .scalper_engine import readiness
        control=ScalperControl.objects.filter(source=feed.instance_id,account_login=feed.account_login,
            server_name=feed.server_name,symbol=feed.symbol).first()
        result.update(broker_readiness=readiness(feed),automatic_entries=bool(control and control.automatic),
            broker_execution_enabled=all(getattr(settings,k,False) for k in ['RAMON_SCALP_EXECUTION_ENABLED',
                'RAMON_EA_COMMAND_CREATION_ENABLED','RAMON_EA_COMMAND_EXECUTION_ENABLED']))
        rows = list(ScalpObservation.objects.filter(source=feed.instance_id, account_login=feed.account_login,
            server_name=feed.server_name, symbol=feed.symbol, version=VERSION).order_by('-recorded_at')[:2000])
        scored = [r for r in rows if r.status == 'SCORED']
        result.update(source=feed.instance_id, symbol=feed.symbol, recorded=len(rows), sample_limit=2000,
            scored=len(scored), pending=sum(r.status=='WAITING' for r in rows),
            skipped=sum(r.status=='SKIPPED' for r in rows),
            reasons=dict(Counter(r.reason for r in rows)),
            net_points=sum(r.outcome['net_points'] for r in scored),
            mean_net_r=sum(r.outcome['net_r'] for r in scored)/len(scored) if scored else None,
            win_rate=sum(r.outcome['net_points']>0 for r in scored)/len(scored) if scored else None,
            recent=[dict(recorded_at=r.recorded_at, action=r.action, reason=r.reason, status=r.status,
                         result=r.outcome.get('result'), net_points=r.outcome.get('net_points')) for r in rows[:10]])
    response = JsonResponse(result, json_dumps_params={'allow_nan':False})
    response['Cache-Control'] = 'no-store'
    return response
