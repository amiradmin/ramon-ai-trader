"""Independent demo scalper ownership and ticket-specific lifecycle.

No symbol-wide close, no retry of uncertain execution, no authority over main
Ramon positions. Both engines share the existing account risk ceiling.
"""
from datetime import timedelta
from decimal import Decimal, ROUND_FLOOR
import hashlib

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import AccountDailyBaseline, DecisionThresholdConfig, EACommand, EAHeartbeat, ScalperControl, ScalpObservation
from .command_producer import _decimal

ENGINE = 'SCALPER'
MAGIC = 26092213
UNSETTLED = ['PENDING','CLAIMED','RECEIVED','EXECUTING','RECONCILIATION_REQUIRED']


def engine_commands(feed):
    return EACommand.objects.filter(instance_id=feed.instance_id, account_login=feed.account_login,
        symbol=feed.symbol, payload__server_name=feed.server_name, payload__engine=ENGINE)


def owned_position(feed, position):
    """A magic number alone never establishes ownership."""
    if not isinstance(position,dict) or position.get('symbol') != feed.symbol or position.get('magic') != MAGIC:
        return None
    matches = list(engine_commands(feed).filter(action='OPEN',state='EXECUTED',execution_authorized=True,
        result__broker_ticket=str(position.get('ticket','')))[:2])
    return matches[0] if len(matches)==1 else None


def limits():
    saved = DecisionThresholdConfig.objects.filter(profile='demo').first()
    risk = saved.values.get('risk_management',{}) if saved else {}
    return (_decimal(risk.get('max_total_risk_percent',2),'portfolio limit'),
            _decimal(risk.get('max_daily_loss_percent',5),'daily limit'))


def exposure_money(feed):
    """All symbols/engines and outstanding OPEN reservations count."""
    positions = feed.payload.get('positions')
    if not isinstance(positions,list):
        raise ValueError('Positions unavailable')
    exposure = Decimal(0)
    for p in positions:
        entry, stop, volume, tick, value = [_decimal(p.get(k),k) for k in
            ['price_open','sl','volume','tick_size','tick_value']]
        if min(entry,stop,volume,tick,value) <= 0 or type(p.get('type')) is not int or p['type'] not in (0,1):
            raise ValueError('Unverified position risk')
        exposure += max(Decimal(0),(entry-stop)*(1 if p['type']==0 else -1))/tick*value*volume
    reserved = EACommand.objects.filter(instance_id=feed.instance_id,account_login=feed.account_login,
        payload__server_name=feed.server_name, action='OPEN', state__in=UNSETTLED)
    for c in reserved:
        amount = _decimal(c.payload.get('reserved_risk_money'),'reserved risk')
        if amount < 0: raise ValueError('Invalid reservation')
        exposure += amount
    # An executed order not yet reflected in later telemetry remains reserved.
    for c in EACommand.objects.filter(instance_id=feed.instance_id,account_login=feed.account_login,
        payload__server_name=feed.server_name,action='OPEN',state='EXECUTED',completed_at__gte=feed.last_seen):
        ticket = str(c.result.get('broker_ticket',''))
        if not any(str(p.get('ticket'))==ticket for p in positions):
            exposure += _decimal(c.payload.get('reserved_risk_money'),'unobserved fill risk')
    return exposure


def readiness(feed, *, now=None):
    now = now or timezone.now()
    p = feed.payload if isinstance(feed.payload,dict) else {}
    if not all(getattr(settings,k,False) for k in ['RAMON_SCALP_EXECUTION_ENABLED',
            'RAMON_EA_COMMAND_CREATION_ENABLED','RAMON_EA_COMMAND_EXECUTION_ENABLED']):
        return 'EXECUTION_DISABLED'
    scope = (getattr(settings,'RAMON_SCALP_INSTANCE',''),getattr(settings,'RAMON_SCALP_LOGIN',''),
             getattr(settings,'RAMON_SCALP_SERVER',''))
    if scope != (feed.instance_id,feed.account_login,feed.server_name): return 'OUTSIDE_SCALPER_SCOPE'
    if type(p.get('account_trade_mode')) is not int or p['account_trade_mode'] != 0: return 'DEMO_REQUIRED'
    if p.get('account_margin_mode') != 2 or type(p.get('account_margin_mode')) is not int:
        return 'HEDGING_TELEMETRY_REQUIRED'
    if p.get('execution_profile') != 'ticket-magic-v1': return 'EA_109_REQUIRED'
    if not feed.terminal_connected or not feed.trade_allowed or feed.status != 'TELEMETRY_OK': return 'TERMINAL_NOT_READY'
    if p.get('server_name') != feed.server_name or not 0 <= (now-feed.last_seen).total_seconds() <= 30:
        return 'TELEMETRY_STALE'
    stamp = parse_datetime(str(p.get('quote_time','')))
    if not stamp or not timezone.is_aware(stamp) or not -5 <= (now-stamp).total_seconds() <= 20:
        return 'QUOTE_STALE'
    return 'READY'


def allow_main_alongside_scalps(feed):
    positions = feed.payload.get('positions')
    return (isinstance(positions,list) and bool(positions) and feed.payload.get('account_margin_mode')==2
            and feed.payload.get('execution_profile')=='ticket-magic-v1'
            and all(owned_position(feed,p) is not None for p in positions))


def _close_scalp(*, feed, ticket, reason='MANUAL', now=None):
    now = now or timezone.now()
    with transaction.atomic():
        feed = EAHeartbeat.objects.select_for_update().get(pk=feed.pk)
        if readiness(feed,now=now) != 'READY': return None, readiness(feed,now=now)
        positions = feed.payload.get('positions')
        if not isinstance(positions,list): return None, 'POSITIONS_UNAVAILABLE'
        pos = next((p for p in positions if str(p.get('ticket',''))==str(ticket)),None)
        original = owned_position(feed,pos)
        if not original: return None, 'NOT_A_SCALPER_POSITION'
        text = str(ticket)
        if not text.isascii() or not text.isdigit() or not 0<int(text)<2**53: return None,'INVALID_TICKET'
        if engine_commands(feed).filter(state__in=UNSETTLED).exists(): return None,'COMMAND_UNSETTLED'
        command_id = 'scalp-close-'+hashlib.sha256(original.command_id.encode()).hexdigest()[:32]
        command, created = EACommand.objects.get_or_create(command_id=command_id,defaults=dict(
            instance_id=feed.instance_id,account_login=feed.account_login,symbol=feed.symbol,action='CLOSE',
            execution_authorized=True,expires_at=now+timedelta(seconds=20),
            payload=dict(server_name=feed.server_name,engine=ENGINE,magic=MAGIC,position_ticket=int(text),
                         original_command_id=original.command_id,close_reason=reason)))
        return command, 'QUEUED' if created else 'EXISTING_CLOSE_NO_RETRY'


def _open_scalp(feed, control, observation, *, now=None, audit_context=None):
    now = now or timezone.now()
    with transaction.atomic():
        feed = EAHeartbeat.objects.select_for_update().get(pk=feed.pk)
        control = ScalperControl.objects.select_for_update().get(pk=control.pk)
        if audit_context is not None:
            audit_context.update(automatic=control.automatic,risk_percent=str(control.risk_percent),
                heartbeat_at=feed.last_seen.isoformat(),quote_time=feed.payload.get('quote_time'),
                quote=feed.payload.get('quote'),account=feed.payload.get('account'),
                positions=feed.payload.get('positions'),symbol_info=feed.payload.get('symbol_info'),
                meaning='Input snapshot inside the engine transaction; not future outcomes')
        if not control.automatic: return None,'AUTOMATIC_PAUSED'
        ready = readiness(feed,now=now)
        if ready != 'READY': return None,ready
        if (control.source,control.account_login,control.server_name,control.symbol) != (
            feed.instance_id,feed.account_login,feed.server_name,feed.symbol): return None,'CONTROL_SCOPE_MISMATCH'
        if (observation.source,observation.account_login,observation.server_name,observation.symbol) != (
            feed.instance_id,feed.account_login,feed.server_name,feed.symbol): return None,'SIGNAL_SCOPE_MISMATCH'
        if observation.action not in {'BUY','SELL'} or not 0 <= (now-observation.recorded_at).total_seconds() <= 15:
            return None,'NO_FRESH_SIGNAL'
        if not 0 <= (now-observation.signal_bar_time-timedelta(minutes=1)).total_seconds() <= 30:
            return None,'SIGNAL_BAR_STALE'
        if engine_commands(feed).filter(state__in=UNSETTLED).exists(): return None,'COMMAND_UNSETTLED'
        if not isinstance(feed.payload.get('positions'),list): return None,'POSITIONS_UNAVAILABLE'
        # Unknown/foreign positions may coexist, but their stops must be measurable.
        # A scalp with this magic that lacks a matched command blocks new scalps.
        if any(p.get('magic')==MAGIC for p in feed.payload['positions']): return None,'SCALPER_POSITION_ALREADY_OPEN'
        try:
            p = feed.payload; info = p['symbol_info']; quote = p['quote']; account = p['account']
            balance, equity = [_decimal(account[k],k) for k in ['balance','equity']]
            if min(balance,equity)<=0: raise ValueError('account')
            cap, daily_cap = limits()
            baseline = AccountDailyBaseline.objects.filter(account_login=feed.account_login,
                server_name=feed.server_name,broker_day=p.get('broker_day')).first()
            if not baseline or baseline.opening_balance<=0: return None,'DAILY_BASELINE_MISSING'
            if (baseline.opening_balance-equity)/baseline.opening_balance*100 >= daily_cap:
                return None,'DAILY_LOSS_LIMIT'
            risk_percent = _decimal(control.risk_percent,'risk percent')
            if not 0 < risk_percent <= Decimal('.15'): return None,'SCALPER_RISK_LIMIT'
            bid,ask,point = [_decimal(quote[k],k) for k in ['bid','ask','point']]
            tick,value,step,minimum,maximum,stops = [_decimal(info[k],k) for k in
                ['tick_size','tick_value','volume_step','volume_min','volume_max','stops_level_points']]
            if min(bid,ask,point,tick,value,step,minimum,maximum)<=0 or ask<bid or stops<0: raise ValueError('contract')
            sign = 1 if observation.action=='BUY' else -1
            entry = ask if sign==1 else bid
            plan = observation.snapshot['plan']
            stop,target = _decimal(plan['stop'],'stop'),_decimal(plan['target'],'target')
            distance,reward = sign*(entry-stop),sign*(target-entry)
            exit_quote = bid if sign==1 else ask
            if min(distance,reward)<=0 or reward/distance<1: return None,'ENTRY_PLAN_INVALID'
            if min(sign*(exit_quote-stop),sign*(target-exit_quote)) <= stops*point: return None,'BROKER_STOP_DISTANCE'
            costs = (ask-bid)+Decimal('15')*point # assumed 5 each side plus 5 round-trip commission
            if costs/distance>Decimal('.20'): return None,'COST_LIMIT'
            headroom = balance*cap/100-exposure_money(feed)
            budget = min(balance*risk_percent/100,headroom)
            per_lot = (distance+Decimal('15')*point)/tick*value # adverse allowance plus commission in reservation
            volume = (min(maximum,budget/per_lot)/step).to_integral_value(rounding=ROUND_FLOOR)*step
            if volume<minimum: return None,'RISK_BUDGET_BELOW_MINIMUM_VOLUME'
            reserved = per_lot*volume
        except (KeyError,TypeError,ValueError,ArithmeticError):
            return None,'RISK_FACTS_UNAVAILABLE'
        command_id = 'scalp-open-'+hashlib.sha256(f'{observation.pk}|{feed.account_login}|{feed.server_name}'.encode()).hexdigest()[:32]
        command,created = EACommand.objects.get_or_create(command_id=command_id,defaults=dict(
            instance_id=feed.instance_id,account_login=feed.account_login,symbol=feed.symbol,action='OPEN',
            execution_authorized=True,expires_at=now+timedelta(seconds=15),
            payload=dict(server_name=feed.server_name,engine=ENGINE,magic=MAGIC,side=observation.action,
                volume=float(volume),stop_loss=float(stop),take_profit=float(target),
                reserved_risk_money=float(reserved),scalp_observation_id=observation.pk,
                market_snapshot=dict(bid=float(bid),ask=float(ask),point=float(point)),
                risk_snapshot=dict(balance=str(balance),equity=str(equity),baseline=str(baseline.opening_balance),
                    total_risk_cap_percent=str(cap),daily_loss_cap_percent=str(daily_cap),
                    risk_percent=str(risk_percent),available_headroom=str(headroom),planned_budget=str(budget)))))
        return command,'QUEUED' if created else 'EXISTING_OPEN_NO_RETRY'


@transaction.atomic
def close_scalp(*, feed, ticket, reason='MANUAL', now=None):
    now = now or timezone.now()
    command, result = _close_scalp(feed=feed,ticket=ticket,reason=reason,now=now)
    from .scalper_audit import feed_event, command_event
    feed_event(feed,'EXIT_GATE',result,identity=[str(ticket),reason,result],command=command,now=now,
        details={'requested_ticket':str(ticket),'close_reason':reason})
    if command:
        command_event(command,'CLOSE_REQUEST',now=now)
    return command, result


@transaction.atomic
def open_scalp(feed, control, observation, *, now=None):
    now = now or timezone.now()
    context = {}
    command, reason = _open_scalp(feed, control, observation, now=now, audit_context=context)
    from .scalper_audit import gate_event
    gate_event(feed,control,observation,command,reason,now,context=context)
    return command, reason


def engine_cycle():
    results = []
    for control in ScalperControl.objects.all():
        feed = EAHeartbeat.objects.filter(instance_id=control.source,account_login=control.account_login,
            server_name=control.server_name,symbol=control.symbol).first()
        if not feed: continue
        # Pause stops entries only. Time exits keep managing owned positions.
        for position in feed.payload.get('positions',[]):
            original = owned_position(feed,position)
            if original and original.completed_at and timezone.now()-original.completed_at>=timedelta(minutes=5):
                _, reason = close_scalp(feed=feed,ticket=position['ticket'],reason='FIVE_MINUTE_TIME_EXIT')
                results.append(reason)
        latest = ScalpObservation.objects.filter(source=feed.instance_id,account_login=feed.account_login,
            server_name=feed.server_name,symbol=feed.symbol).order_by('-recorded_at').first()
        if latest:
            _, reason = open_scalp(feed,control,latest)
            results.append(reason)
    return results
