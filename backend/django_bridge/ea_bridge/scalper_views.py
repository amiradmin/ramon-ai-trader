"""Staff session + CSRF protected controls; never exposes the EA bearer token."""
import uuid
from decimal import Decimal
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.http import require_http_methods
from .models import EAEvent, EAHeartbeat, EACommand, ScalperControl, LearningTradeOutcome
from django.db.models import Sum, Count
from django.db import transaction
from .scalper_engine import engine_commands, owned_position, readiness, close_scalp


def _set_scalper_entries(request, action, scope):
    """Apply the existing scalp readiness and account scope to dashboard controls."""
    with transaction.atomic():
        feed = EAHeartbeat.objects.filter(status='TELEMETRY_OK').order_by('-last_seen').first()
        control = ScalperControl.objects.select_for_update().filter(
            source=feed.instance_id, account_login=feed.account_login,
            server_name=feed.server_name, symbol=feed.symbol,
        ).first() if feed else None
        if not control or str(control.pk) != str(scope):
            return False, 'Scalper scope changed. Reload before using controls.'
        if action == 'pause':
            control.automatic = False
            control.save(update_fields=['automatic'])
            EACommand.objects.filter(instance_id=feed.instance_id,account_login=feed.account_login,
                symbol=feed.symbol,payload__server_name=feed.server_name,payload__engine='SCALPER',
                action='OPEN',state='PENDING').update(state='EXPIRED',completed_at=timezone.now())
            message = 'Automatic scalp entries paused; queued entries expired. Already claimed orders may finish; existing exits remain managed.'
            success = True
        elif action == 'start' and readiness(feed) == 'READY':
            control.automatic = True
            control.save(update_fields=['automatic'])
            message = 'Automatic real cent scalping enabled; saved risk and shared account limits apply.'
            success = True
        else:
            message = 'Cannot enable Fast Scalp: '+readiness(feed)
            success = False
        EAEvent.objects.create(instance_id=feed.instance_id,event_id='scalper-control-'+uuid.uuid4().hex,
            event_type='SCALPER_CONTROL',payload=dict(user=request.user.get_username(),action=action,
                account_login=feed.account_login,server_name=feed.server_name,message=message))
        return success,message


@staff_member_required
@require_POST
def scalper_entries_control(request):
    action=request.POST.get('action')
    if action not in ('start','pause'):
        return JsonResponse({'ok':False,'error':'Unsupported action.'},status=400)
    success,message=_set_scalper_entries(request,action,request.POST.get('scope'))
    response=JsonResponse({'ok':success,'message':message},status=200 if success else 409)
    response['Cache-Control']='no-store'
    return response


@staff_member_required
@require_http_methods(['GET','POST'])
def scalper_console(request):
    feed = EAHeartbeat.objects.filter(status='TELEMETRY_OK').order_by('-last_seen').first()
    message = ''
    control = None
    if feed:
        control = ScalperControl.objects.filter(source=feed.instance_id,account_login=feed.account_login,
            server_name=feed.server_name,symbol=feed.symbol).first()
    if request.method == 'POST' and request.POST.get('action') in ('start','pause'):
        _,message = _set_scalper_entries(request,request.POST['action'],request.POST.get('scope'))
        control.refresh_from_db() if control else None
    elif request.method == 'POST':
        if not feed or not control or request.POST.get('scope') != str(control.pk):
            message = 'Scalper scope changed. Reload before submitting a control.'
        elif request.POST.get('action') == 'close':
            command, reason = close_scalp(feed=feed,ticket=request.POST.get('ticket',''))
            message = reason + (' · '+command.command_id if command else '')
            EAEvent.objects.create(instance_id=feed.instance_id,event_id='scalper-control-'+uuid.uuid4().hex,
                event_type='SCALPER_CONTROL',payload=dict(user=request.user.get_username(),action='close',
                    account_login=feed.account_login,server_name=feed.server_name,message=message))
        else:
            message = 'Unknown control.'
    positions = []
    if feed:
        for p in feed.payload.get('positions',[]):
            if owned_position(feed,p): positions.append(p)
    closed = LearningTradeOutcome.objects.filter(instance_id=feed.instance_id, account_login=feed.account_login,
        symbol=feed.symbol, trade_id__startswith=f"{feed.account_login}:{feed.server_name}:",
        tags__strategy_profile="SCALPER_M1_M5").aggregate(trades=Count('id'), net_pnl=Sum('realized_pnl')) if feed else {}
    return render(request,'ea_bridge/scalper_console.html' ,dict(feed=feed,control=control,message=message,
        ready=readiness(feed) if feed else 'NO_FEED',positions=positions,closed=closed,
        recent=engine_commands(feed).order_by('-created_at')[:20] if feed else []))


@require_GET
def scalper_status(request):
    """Read-only status for the Fast Scalp dashboard card."""
    feed = EAHeartbeat.objects.filter(status='TELEMETRY_OK').order_by('-last_seen').first()
    control = ScalperControl.objects.filter(
        source=feed.instance_id, account_login=feed.account_login,
        server_name=feed.server_name, symbol=feed.symbol,
    ).first() if feed else None
    fresh = bool(
        feed and feed.terminal_connected
        and 0 <= (timezone.now() - feed.last_seen).total_seconds() <= 60
        and isinstance(feed.payload, dict)
        and feed.payload.get('server_name') == feed.server_name
    )
    positions_known = bool(fresh and control and isinstance(feed.payload.get('positions'), list))
    positions = sum(owned_position(feed, position) is not None
                    for position in feed.payload['positions']) if positions_known else 0
    from .scalper_diagnostics import authorization_summary, latest_entry_evidence
    # The public status endpoint must not expose account balances or risk money.
    evidence = latest_entry_evidence(feed) if feed and request.user.is_staff else {}
    sizing = evidence.get('sizing') or {}
    evidence['sizing'] = {k: sizing[k] for k in (
        'calculated_volume','min_volume','minimum_required_risk_percent') if k in sizing}
    response = JsonResponse({
        'authorization': authorization_summary(feed) if feed else None,
        'entry_evidence': evidence,
        'fresh': fresh,
        'automatic': bool(control and control.automatic),
        'control_scope': control.pk if control else None,
        'readiness': readiness(feed) if feed else 'NO_FEED',
        'position_status_known': positions_known,
        'open_positions': positions,
    })
    response['Cache-Control'] = 'no-store'
    return response
