"""Staff session + CSRF protected controls; never exposes the EA bearer token."""
import uuid
from decimal import Decimal
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render, redirect
from django.views.decorators.http import require_http_methods
from .models import EAEvent, EAHeartbeat, ScalperControl, LearningTradeOutcome
from django.db.models import Sum, Count
from .scalper_engine import engine_commands, owned_position, readiness, close_scalp


@staff_member_required
@require_http_methods(['GET','POST'])
def scalper_console(request):
    feed = EAHeartbeat.objects.filter(status='TELEMETRY_OK').order_by('-last_seen').first()
    message = ''
    control = None
    if feed:
        control = ScalperControl.objects.filter(source=feed.instance_id,account_login=feed.account_login,
            server_name=feed.server_name,symbol=feed.symbol).first()
    if request.method == 'POST':
        if not feed or not control:
            message = 'No configured scalper feed. Controls are unavailable.'
        elif request.POST.get('scope') != str(control.pk):
            message = 'Account scope changed. Reload before submitting a control.'
        else:
            action = request.POST.get('action')
            if action == 'pause':
                ScalperControl.objects.filter(pk=control.pk).update(automatic=False)
                message = 'Automatic scalp entries paused. Existing scalp exits remain managed.'
            elif action == 'start':
                ready = readiness(feed)
                if ready == 'READY':
                    ScalperControl.objects.filter(pk=control.pk).update(automatic=True)
                    message = 'Automatic demo scalping enabled at a maximum 0.15% planned risk per trade.'
                else:
                    message = 'Cannot start: '+ready
            elif action == 'close':
                command, reason = close_scalp(feed=feed,ticket=request.POST.get('ticket',''))
                message = reason + (' · '+command.command_id if command else '')
            else:
                message = 'Unknown control.'
            EAEvent.objects.create(instance_id=feed.instance_id,event_id='scalper-control-'+uuid.uuid4().hex,
                event_type='SCALPER_CONTROL',payload=dict(user=request.user.get_username(),action=action,
                    account_login=feed.account_login,server_name=feed.server_name,message=message))
            control.refresh_from_db()
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
