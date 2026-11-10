"""One dashboard page per independently operated strategy container."""
import uuid
from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db import transaction
from django.db.models import Sum, Count
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from .models import StrategyWorkerControl, DecisionEvaluation, EACommand, EAEvent, LearningTradeOutcome
from .strategy_registry import STRATEGIES, strategy_commands, owned_strategy_position
from .strategy_worker import configured_feed, strategy_readiness, close_strategy_position


def worker_rows():
    now=timezone.now()
    controls={c.setup:c for c in StrategyWorkerControl.objects.all()}
    feed=configured_feed()
    position_feed_fresh=bool(feed and feed.status=='TELEMETRY_OK' and feed.terminal_connected
        and 0<=(now-feed.last_seen).total_seconds()<=60
        and feed.payload.get('server_name')==feed.server_name
        and isinstance(feed.payload.get('positions'),list))
    result=[]
    for setup,(slug,description,magic) in STRATEGIES.items():
        c=controls.get(setup)
        fresh=bool(c and c.last_seen and 0<=(now-c.last_seen).total_seconds()<90)
        position_status_known=bool(position_feed_fresh and c and
            (c.source,c.account_login,c.server_name,c.symbol)==
            (feed.instance_id,feed.account_login,feed.server_name,feed.symbol))
        open_positions=sum(owned_strategy_position(feed,p,setup=setup) is not None
            for p in feed.payload['positions']) if position_status_known else 0
        result.append(dict(open_positions=open_positions,position_status_known=position_status_known,setup=setup,slug=slug,description=description,container='ramon-'+slug,
            url='/api/ea/v1/strategies/'+slug,fresh=fresh,automatic=bool(c and c.automatic),
            state=c.state if c else 'NOT_STARTED',last_seen=c.last_seen if c else None,
            latest=c.latest if c else {},magic=magic))
    return result


@require_GET
def strategies_status(request):
    response=JsonResponse(dict(mode='INDEPENDENT_STRATEGY_CONTAINERS',
        monolith_retired=settings.RAMON_STRATEGY_CONTAINERS_ENABLED,workers=worker_rows()))
    response['Cache-Control']='no-store'
    return response


@staff_member_required
@require_http_methods(['GET','POST'])
def strategy_console(request,slug):
    setup=next((name for name,item in STRATEGIES.items() if item[0]==slug),None)
    if setup is None: raise Http404
    feed=configured_feed()
    control=StrategyWorkerControl.objects.filter(setup=setup).first()
    if request.method=='POST':
        if not control or request.POST.get('scope')!=str(control.pk):
            messages.error(request,'Strategy scope changed. Reload before using controls.')
            return redirect(request.path)
        with transaction.atomic():
            control=StrategyWorkerControl.objects.select_for_update().get(pk=control.pk)
            action=request.POST.get('action')
            ready=strategy_readiness(feed)
            scope_matches=bool(feed and (control.source,control.account_login,control.server_name,control.symbol)==
                (feed.instance_id,feed.account_login,feed.server_name,feed.symbol))
            fresh=bool(control.last_seen and 0<=(timezone.now()-control.last_seen).total_seconds()<90)
            if action=='pause':
                control.automatic=False;control.save(update_fields=['automatic'])
                EACommand.objects.filter(instance_id=control.source,account_login=control.account_login,
                    symbol=control.symbol,payload__server_name=control.server_name,payload__engine='STRATEGY',
                    payload__strategy_setup=setup,action='OPEN',state='PENDING').update(state='EXPIRED',completed_at=timezone.now())
                text='Paused this strategy and expired its queued entries. Already claimed orders may finish; existing positions retain exits.'
            elif action=='start' and scope_matches and fresh and ready=='READY' and settings.RAMON_STRATEGY_CONTAINERS_ENABLED and all(
                    getattr(settings,k,False) for k in ['RAMON_EA_COMMAND_CREATION_ENABLED','RAMON_EA_COMMAND_EXECUTION_ENABLED']):
                control.automatic=True;control.save(update_fields=['automatic'])
                text='Automatic demo entries enabled for this strategy only.'
            elif action=='close' and scope_matches:
                _,text=close_strategy_position(setup,request.POST.get('ticket',''))
            else:
                text='Cannot perform action: check worker health, account scope, readiness and execution gates.'
            EAEvent.objects.create(instance_id=control.source,event_id='setup-control-'+uuid.uuid4().hex,
                event_type='STRATEGY_CONTROL',payload=dict(setup=setup,user=request.user.get_username(),action=action,message=text))
            messages.info(request,text)
        return redirect(request.path)
    worker=next(row for row in worker_rows() if row['setup']==setup)
    positions=[]; commands=[]; evaluations=[];closed={'trades':0,'net_pnl':None}
    if control:
        evaluations=DecisionEvaluation.objects.filter(instance_id=control.source,account_login=control.account_login,
            server_name=control.server_name,symbol=control.symbol,features__strategy_setup=setup).order_by('-created_at')[:30]
        commands=EACommand.objects.filter(instance_id=control.source,account_login=control.account_login,symbol=control.symbol,
            payload__server_name=control.server_name,payload__engine='STRATEGY',payload__strategy_setup=setup).order_by('-created_at')[:30]
        closed=LearningTradeOutcome.objects.filter(instance_id=control.source,account_login=control.account_login,
            symbol=control.symbol,trade_id__startswith=f'{control.account_login}:{control.server_name}:',
            tags__strategy_profile='SETUP_'+setup).aggregate(trades=Count('id'),net_pnl=Sum('realized_pnl'))
    if feed:
        positions=[p for p in feed.payload.get('positions',[]) if owned_strategy_position(feed,p,setup=setup)]
    return render(request,'ea_bridge/strategy_console.html',dict(worker=worker,control=control,
        ready=strategy_readiness(feed),positions=positions,commands=commands,evaluations=evaluations,closed=closed,
        execution_enabled=settings.RAMON_STRATEGY_CONTAINERS_ENABLED and settings.RAMON_EA_COMMAND_CREATION_ENABLED and settings.RAMON_EA_COMMAND_EXECUTION_ENABLED))
