"""Django-owned demo breakeven planning and auditable, once-only commands.

MT5 supplies quotes/positions and executes commands. All policy calculations live
here. No historical maximum is reconstructed when this feature is first enabled.
"""
import hashlib
from datetime import timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .command_producer import _decimal, _is_demo_account
from .models import EACommand, EAHeartbeat, LearningTradeOutcome


def protection_status(feed, *, strategy_setup=None):
    enabled = bool(getattr(settings, 'RAMON_DEMO_PROFIT_PROTECTION_ENABLED', False))
    result = dict(mode='DEMO_ENABLED' if enabled else 'OBSERVE_ONLY', calculation_owner='DJANGO',
        rule='At +1 original R, move SL to entry; preserve TP. Price breakeven excludes costs.',
        positions=[], recent_commands=[])
    scope=(getattr(settings,'RAMON_PROFIT_PROTECTION_INSTANCE',''),
        getattr(settings,'RAMON_PROFIT_PROTECTION_LOGIN',''),getattr(settings,'RAMON_PROFIT_PROTECTION_SERVER',''))
    if scope != (feed.instance_id,feed.account_login,feed.server_name):
        result['state']='OUTSIDE_CONFIGURED_DEMO_SCOPE'
        return result
    now = timezone.now()
    p = feed.payload if isinstance(feed.payload, dict) else {}
    age = (now-feed.last_seen).total_seconds()
    stamp = parse_datetime(str(p.get('quote_time','')))
    if (not _is_demo_account(p) or age < 0 or age > 60 or not stamp or not stamp.tzinfo
        or not -5 <= (now-stamp).total_seconds() <= 30 or feed.status!='TELEMETRY_OK'
        or not feed.terminal_connected or not feed.trade_allowed
        or p.get('server_name') != feed.server_name or not feed.server_name):
        result['state']='TELEMETRY_NOT_READY'
        return result
    try:
        bid, ask, point = [_decimal(p.get('quote',{}).get(k),k) for k in ['bid','ask','point']]
        tick = _decimal(p.get('symbol_info',{}).get('tick_size'),'tick_size')
        stops = _decimal(p.get('symbol_info',{}).get('stops_level_points'),'stops_level_points')*point
        if min(bid,ask,point,tick)<=0 or ask<bid or stops<0: raise ValueError('quote')
    except (ValueError,TypeError,AttributeError):
        result['state']='INVALID_QUOTE_METADATA'
        return result
    commands_query = EACommand.objects.filter(instance_id=feed.instance_id, account_login=feed.account_login,
        symbol=feed.symbol, action='OPEN', state='EXECUTED', execution_authorized=True)
    if strategy_setup:
        commands_query = commands_query.filter(payload__engine='STRATEGY',payload__strategy_setup=strategy_setup)
    else:
        commands_query = commands_query.filter(Q(payload__engine__isnull=True) | ~Q(payload__engine__in=['SCALPER','STRATEGY']))
    commands = list(commands_query.order_by('-created_at')[:200])
    positions = p.get('positions',[])
    if not isinstance(positions,list):
        result['state']='INVALID_POSITIONS'
        return result
    for pos in positions:
        row = dict(ticket=str(pos.get('ticket','')) if isinstance(pos,dict) else '',state='UNVERIFIED_POSITION')
        result['positions'].append(row)
        if not isinstance(pos,dict) or pos.get('symbol')!=feed.symbol: continue
        # Legacy EA JsonNumber reads JSON numbers, not quoted ticket strings.
        # IEEE-754 exactness is required because MQL parses through double.
        ticket_text=row['ticket']
        if not ticket_text.isascii() or not ticket_text.isdigit() or not 0<int(ticket_text)<=2**53-1:
            row['state']='UNSUPPORTED_TICKET'
            continue
        matched = [c for c in commands if isinstance(c.result,dict) and str(c.result.get('broker_ticket',''))==row['ticket']
            and c.payload.get('server_name')==feed.server_name]
        if len(matched)!=1: continue
        original = matched[0]
        if strategy_setup:
            from .strategy_registry import owned_strategy_position
            if owned_strategy_position(feed,pos,setup=strategy_setup) is None: continue
        try:
            side=original.payload['side']; sign=Decimal(1 if side=='BUY' else -1)
            if side not in {'BUY','SELL'} or type(pos.get('type')) is not int or pos['type']!=(0 if side=='BUY' else 1): continue
            entry,sl,tp,volume=[_decimal(pos.get(k),k) for k in ['price_open','sl','tp','volume']]
            initial_sl=_decimal(original.payload['stop_loss'],'initial stop')
            initial_volume=_decimal(original.payload['volume'],'initial volume')
            initial_tp=_decimal(original.payload['take_profit'],'initial target')
            if min(entry,sl,tp,volume,initial_sl)<=0 or volume!=initial_volume or abs(tp-initial_tp)>tick: continue
            risk=sign*(entry-initial_sl)
            if risk<=0: continue
            executable=bid if side=='BUY' else ask
            r=sign*(executable-entry)/risk
            proposed=(entry/tick).to_integral_value(rounding=ROUND_CEILING if side=='BUY' else ROUND_FLOOR)*tick
            command_id='ramon-protect-'+hashlib.sha256(original.command_id.encode()).hexdigest()[:32]
            previous=EACommand.objects.filter(command_id=command_id,instance_id=feed.instance_id,
                account_login=feed.account_login,symbol=feed.symbol).first()
            # One bounded recovery ONLY for the proven quoted-ticket defect:
            # the old EA attempted ticket 0, so this request never modified this
            # position. Preserve the failed row; recheck current eligibility.
            if (previous and previous.state=='FAILED' and isinstance(previous.payload,dict)
                and previous.payload.get('position_ticket')==ticket_text
                and isinstance(previous.result,dict) and previous.result.get('broker_ticket')=='0'
                and previous.result.get('detail')=='BROKER_MODIFY_0_unknown retcode 0'):
                command_id='ramon-protect-'+hashlib.sha256((original.command_id+'|numeric-ticket-v2').encode()).hexdigest()[:32]
            row.update(side=side,entry=float(entry),original_risk=float(risk),current_r=float(r),
                current_stop=float(sl),proposed_stop=float(proposed),take_profit=float(tp),command_id=command_id,
                original_command_id=original.command_id,decision_id=original.decision_id,risk_verdict_id=original.risk_verdict_id)
            if sign*(sl-proposed)>=0: row['state']='ALREADY_PROTECTED'
            elif r<1: row['state']='WAITING_FOR_1R'
            elif sign*(executable-proposed)<=max(stops,tick): row['state']='BROKER_DISTANCE_TOO_SMALL'
            elif sign*(tp-proposed)<=0: row['state']='INVALID_TARGET'
            else: row['state']='READY'
            existing=EACommand.objects.filter(command_id=command_id).first()
            if existing and row['state']!='ALREADY_PROTECTED':
                row['eligibility_state']=row['state']
                row['state']='COMMAND_'+existing.state
        except (KeyError,ValueError,TypeError): continue
    recent=EACommand.objects.filter(instance_id=feed.instance_id,account_login=feed.account_login,
        symbol=feed.symbol,action='MODIFY_SLTP',command_id__startswith='ramon-protect-').order_by('-created_at')[:20]
    result['recent_commands']=[dict(command_id=c.command_id,state=c.state,payload=c.payload,result=c.result,
        created_at=c.created_at.isoformat(),completed_at=c.completed_at.isoformat() if c.completed_at else None) for c in recent]
    for command, row in zip(recent, result['recent_commands']):
        row['closed_result'] = None
        if command.state!='EXECUTED' or not command.completed_at or not command.decision_id:
            continue
        outcomes=list(LearningTradeOutcome.objects.filter(instance_id=feed.instance_id,
            account_login=feed.account_login,symbol=feed.symbol,decision_id=command.decision_id,
            opened_at__lte=command.completed_at,closed_at__gte=command.completed_at)[:2])
        if len(outcomes)==1:
            outcome=outcomes[0]
            row['closed_result']=dict(net_pnl=float(outcome.realized_pnl),
                closed_at=outcome.closed_at.isoformat(),trade_id=outcome.trade_id)
    result['state']='MONITORING'
    return result


def create_protection_commands(instance_id, account_login, *, strategy_setup=None):
    if not all(getattr(settings,k,False) for k in ['RAMON_DEMO_PROFIT_PROTECTION_ENABLED',
        'RAMON_EA_COMMAND_CREATION_ENABLED','RAMON_EA_COMMAND_EXECUTION_ENABLED']): return
    with transaction.atomic():
        feed=EAHeartbeat.objects.select_for_update().filter(instance_id=instance_id,account_login=account_login).first()
        if not feed: return
        status=protection_status(feed,strategy_setup=strategy_setup)
        for row in status['positions']:
            if row['state']!='READY': continue
            # A pending manual/automatic change must settle first. Never retry an
            # uncertain or failed command; its result stays visible for review.
            pending = EACommand.objects.filter(instance_id=instance_id,account_login=account_login,symbol=feed.symbol,
                state__in=['PENDING','CLAIMED','RECEIVED','EXECUTING','RECONCILIATION_REQUIRED'])
            if strategy_setup:
                pending = pending.filter(payload__engine='STRATEGY',payload__strategy_setup=strategy_setup)
            if pending.exists(): continue
            engine_payload = {}
            if strategy_setup:
                from .strategy_registry import STRATEGIES
                engine_payload = dict(engine='STRATEGY',strategy_setup=strategy_setup,magic=STRATEGIES[strategy_setup][2])
            EACommand.objects.get_or_create(command_id=row['command_id'],defaults=dict(
                instance_id=instance_id,account_login=account_login,symbol=feed.symbol,action='MODIFY_SLTP',
                decision_id=row['decision_id'],risk_verdict_id=row['risk_verdict_id'],execution_authorized=True,
                expires_at=timezone.now()+timedelta(seconds=20),payload=dict(server_name=feed.server_name,
                position_ticket=int(row['ticket']),stop_loss=row['proposed_stop'],take_profit=row['take_profit'],
                protection_rule='BREAKEVEN_AT_1R',calculation_owner='DJANGO',original_risk=row['original_risk'],
                trigger_r=row['current_r'],original_command_id=row['original_command_id'],**engine_payload)))
