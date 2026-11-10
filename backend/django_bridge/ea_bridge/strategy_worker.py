"""Independent strategy processes; shared transport and serialized account budget."""
from datetime import timedelta
import hashlib
import json

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import EAHeartbeat, MarketBar, StrategyWorkerControl, EACommand
from .strategy_registry import STRATEGIES, strategy_commands, owned_strategy_position


def configured_feed():
    return EAHeartbeat.objects.filter(instance_id=settings.RAMON_STRATEGY_INSTANCE,
        account_login=settings.RAMON_STRATEGY_LOGIN,server_name=settings.RAMON_STRATEGY_SERVER).first()


def strategy_readiness(feed, *, now=None):
    now=now or timezone.now()
    if feed is None: return 'CONFIGURED_FEED_MISSING'
    p=feed.payload
    if type(p.get('account_trade_mode')) is not int or p['account_trade_mode']!=0: return 'DEMO_REQUIRED'
    if type(p.get('account_margin_mode')) is not int or p['account_margin_mode']!=2: return 'HEDGING_REQUIRED'
    if p.get('execution_profile')!='ticket-magic-v1': return 'EA_109_REQUIRED'
    if p.get('server_name')!=feed.server_name: return 'SERVER_MISMATCH'
    if not feed.terminal_connected or not feed.trade_allowed or feed.status!='TELEMETRY_OK': return 'TERMINAL_NOT_READY'
    if not 0<=(now-feed.last_seen).total_seconds()<=60: return 'TELEMETRY_STALE'
    stamp=parse_datetime(str(p.get('quote_time','')))
    if not stamp or not timezone.is_aware(stamp) or not -5<=(now-stamp).total_seconds()<=30: return 'QUOTE_STALE'
    return 'READY'


def worker_cycle(setup, *, now=None):
    if setup not in STRATEGIES: raise ValueError('Unknown strategy')
    now=now or timezone.now(); feed=configured_feed()
    defaults=dict(source=settings.RAMON_STRATEGY_INSTANCE,account_login=settings.RAMON_STRATEGY_LOGIN,
        server_name=settings.RAMON_STRATEGY_SERVER,symbol=feed.symbol if feed else '')
    StrategyWorkerControl.objects.get_or_create(setup=setup,defaults=defaults)
    with transaction.atomic():
        control=StrategyWorkerControl.objects.select_for_update().get(setup=setup)
        control.last_seen=now
        def finish(state):
            control.state=state
            control.save(update_fields=['last_seen','state','latest','symbol'])
            return state
        if (control.source,control.account_login,control.server_name)!=(
            defaults['source'],defaults['account_login'],defaults['server_name']): return finish('CONTROL_SCOPE_CHANGED')
        if feed and not control.symbol: control.symbol=feed.symbol
        if feed and control.symbol!=feed.symbol: return finish('CONTROL_SYMBOL_CHANGED')
        ready=strategy_readiness(feed,now=now)
        if ready!='READY': return finish(ready)
        # Existing owned positions retain their independent protection even while paused.
        from .profit_protection import create_protection_commands
        create_protection_commands(feed.instance_id,feed.account_login,strategy_setup=setup)
        if not control.automatic: return finish('PAUSED')
        if not settings.RAMON_STRATEGY_CONTAINERS_ENABLED: return finish('STRATEGY_CONTAINERS_DISABLED')
        stamp=parse_datetime(str(control.latest.get('evaluated_at','')))
        if stamp and 0<=(now-stamp).total_seconds()<30: return finish(control.state)
        rows=list(MarketBar.objects.filter(source=feed.instance_id,symbol=feed.symbol,timeframe='M15',
            timestamp__lte=now-timedelta(minutes=15),received_at__lte=now).order_by('-timestamp')[:256])[::-1]
        if len(rows)<128: return finish('M15_WARMUP')
        if not 0<=(now-rows[-1].timestamp-timedelta(minutes=15)).total_seconds()<900: return finish('M15_STALE')
        quote=feed.payload.get('quote',{})
        payload=dict(instance_id=feed.instance_id,account_login=feed.account_login,server_name=feed.server_name,
            symbol=feed.symbol,timeframe='M15',bid=quote.get('bid'),ask=quote.get('ask'),point=quote.get('point'),
            bars=[dict(time=int(r.timestamp.timestamp()),open=float(r.open),high=float(r.high),low=float(r.low),
                close=float(r.close),closed=True) for r in rows])
        from .decision import evaluate_payload
        response=evaluate_payload(payload,strategy_setup=setup)
        data=json.loads(response.content)
        control.latest=dict(evaluated_at=now.isoformat(),action=data.get('decision','WAIT'),
            reason=data.get('reason',data.get('error','EVALUATION_ERROR')),
            evaluation_id=data.get('evaluation_id'),command_id=data.get('command_id'),
            command_reason=data.get('command_reason'),command_created=data.get('command_created',False))
        return finish(data.get('command_reason') if data.get('command_created') else control.latest['reason'])


@transaction.atomic
def close_strategy_position(setup, ticket):
    feed=configured_feed()
    if setup not in STRATEGIES or strategy_readiness(feed)!='READY': return None,'STRATEGY_NOT_READY'
    if not all(getattr(settings,k,False) for k in ['RAMON_EA_COMMAND_CREATION_ENABLED','RAMON_EA_COMMAND_EXECUTION_ENABLED']):
        return None,'EXECUTION_DISABLED'
    feed=EAHeartbeat.objects.select_for_update().get(pk=feed.pk)
    if strategy_readiness(feed)!='READY': return None,'STRATEGY_NOT_READY'
    positions=feed.payload.get('positions',[])
    if not isinstance(positions,list): return None,'POSITIONS_UNAVAILABLE'
    position=next((p for p in positions if str(p.get('ticket'))==str(ticket)),None)
    original=owned_strategy_position(feed,position,setup=setup)
    if original is None: return None,'NOT_OWNED_BY_THIS_STRATEGY'
    text=str(ticket)
    if not text.isascii() or not text.isdigit() or not 0<int(text)<2**53: return None,'INVALID_TICKET'
    unsettled=strategy_commands(feed,setup).filter(state__in=['PENDING','CLAIMED','RECEIVED','EXECUTING','RECONCILIATION_REQUIRED'])
    if unsettled.exists(): return None,'STRATEGY_COMMAND_UNSETTLED'
    key='setup-close-'+hashlib.sha256(original.command_id.encode()).hexdigest()[:32]
    command,created=EACommand.objects.get_or_create(command_id=key,defaults=dict(instance_id=feed.instance_id,
        account_login=feed.account_login,symbol=feed.symbol,action='CLOSE',execution_authorized=True,
        expires_at=timezone.now()+timedelta(seconds=30),payload=dict(server_name=feed.server_name,
            engine='STRATEGY',strategy_setup=setup,magic=STRATEGIES[setup][2],position_ticket=int(text),
            original_command_id=original.command_id,close_reason='MANUAL_STRATEGY_TICKET')))
    return command,'QUEUED' if created else 'EXISTING_CLOSE_NO_RETRY'
