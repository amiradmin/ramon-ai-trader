"""Read-only, staff-only Fast Scalp diagnostic snapshot."""
from datetime import timedelta
from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

from .models import (EAHeartbeat, ScalperControl, ScalpObservation,
                     ScalpAuditEvent, AccountDailyBaseline, MarketBar)
from .scalper_engine import readiness, engine_commands, limits, exposure_money
from .scalper_audit import provenance

FLAGS = ('RAMON_SCALP_EXECUTION_ENABLED', 'RAMON_EA_COMMAND_CREATION_ENABLED',
         'RAMON_EA_COMMAND_EXECUTION_ENABLED', 'RAMON_REAL_EXECUTION_ENABLED')


def counts(query, *fields):
    return list(query.values(*fields).annotate(count=Count('pk')).order_by('-count', *fields))


def recent(query, limit, ordering, *fields):
    return list(query.order_by(ordering, '-pk').values(*fields)[:limit])


def authorization_summary(feed):
    from .account_scope import real_cent_scope
    allowed, reason = real_cent_scope(feed)
    return dict(policy='INDEPENDENT_SCALPER_REAL_CENT_SCOPE',
                scope_verified=allowed, scope_reason=reason,
                main_real_execution_flag=bool(getattr(settings, 'RAMON_REAL_EXECUTION_ENABLED', False)),
                main_flag_is_scalper_gate=False,
                readiness=readiness(feed))


def latest_entry_evidence(feed):
    scope = dict(source=feed.instance_id, account_login=feed.account_login,
                 server_name=feed.server_name, symbol=feed.symbol)
    gates = ScalpAuditEvent.objects.filter(**scope, kind='ENTRY_GATE')
    fields = ('observed_at', 'reason', 'observation_id', 'details')
    latest = gates.order_by('-observed_at', '-pk').values(*fields).first()
    # Older workers did not persist sizing. Keep the timestamp and do not
    # silently replace missing historical evidence with a current estimate.
    rejected = gates.filter(reason='RISK_BUDGET_BELOW_MINIMUM_VOLUME').order_by(
        '-observed_at', '-pk').values(*fields).first()
    details = (rejected or {}).get('details') or {}
    sizing = (details.get('engine_inputs') or {}).get('sizing')
    return dict(latest_gate_reason=(latest or {}).get('reason'),
                latest_gate_at=(latest or {}).get('observed_at'),
                risk_rejection_at=(rejected or {}).get('observed_at'),
                sizing=sizing, sizing_status='RECORDED' if sizing else 'UNAVAILABLE')


def build_report(now=None):
    now = now or timezone.now()
    since = now - timedelta(days=7)
    report = dict(schema_version=2, generated_at=now, window_start=since,
                  strategy='FAST_SCALP', read_only=True,
                  flags={key: bool(getattr(settings, key, False)) for key in FLAGS},
                  provenance=provenance(),
                  sample_limits={'observations': 300, 'audit_events': 500,
                                 'commands': 200, 'bars_per_timeframe': 100})
    feed = EAHeartbeat.objects.filter(status='TELEMETRY_OK').order_by('-last_seen').first()
    if not feed:
        report['readiness'] = 'NO_FEED'
        report['heartbeat_statuses'] = counts(EAHeartbeat.objects.all(), 'status')
        return report
    scope = dict(source=feed.instance_id, account_login=feed.account_login,
                 server_name=feed.server_name, symbol=feed.symbol)
    report['scope'] = scope
    payload = feed.payload if isinstance(feed.payload, dict) else {}
    report['heartbeat'] = dict(last_seen=feed.last_seen, age_seconds=(now-feed.last_seen).total_seconds(),
                               ea_version=feed.ea_version, status=feed.status,
                               terminal_connected=feed.terminal_connected, trade_allowed=feed.trade_allowed,
                               telemetry={k: payload.get(k) for k in
                                  ('quote_time','quote','account','symbol_info','positions','broker_day',
                                   'account_margin_mode','execution_profile','server_name')})
    report['readiness'] = readiness(feed, now=now)
    report['authorization'] = authorization_summary(feed)
    report['latest_entry_evidence'] = latest_entry_evidence(feed)
    report['control'] = ScalperControl.objects.filter(**scope).values(
        'automatic','risk_percent','updated_at').first()
    cap, daily_cap = limits()
    report['risk_limits'] = dict(portfolio_percent=cap, daily_loss_percent=daily_cap)
    try:
        report['current_exposure_money'] = exposure_money(feed)
    except (ValueError, TypeError, KeyError, ArithmeticError):
        report['current_exposure_money'] = None
        report['exposure_status'] = 'UNAVAILABLE'
    report['daily_baselines'] = list(AccountDailyBaseline.objects.filter(
        account_login=feed.account_login, server_name=feed.server_name
    ).order_by('-broker_day').values('broker_day','opening_balance','capture_method')[:8])
    obs = ScalpObservation.objects.filter(**scope, recorded_at__gte=since, recorded_at__lte=now)
    audit = ScalpAuditEvent.objects.filter(**scope, observed_at__gte=since, observed_at__lte=now)
    commands = engine_commands(feed).filter(created_at__gte=since, created_at__lte=now)
    report['signal_counts'] = counts(obs, 'action','reason')
    report['audit_counts'] = counts(audit, 'kind','reason')
    report['command_counts'] = counts(commands, 'action','state')
    report['latest_observations'] = recent(obs, 300, '-recorded_at',
        'id','recorded_at','signal_bar_time','action','reason','status','snapshot')
    # Policy source bundles are already represented by provenance hashes.
    report['latest_audit_events'] = recent(audit.exclude(kind='POLICY_ARTIFACT'), 500,
        '-observed_at','observed_at','kind','reason','observation_id','command_id','details')
    report['latest_entry_gates'] = recent(audit.filter(kind='ENTRY_GATE'), 200,
        '-observed_at','observed_at','reason','observation_id','command_id','details')
    # Retain rare risk rejections independently of high-frequency WAIT events.
    risk_gates = audit.filter(kind='ENTRY_GATE', reason='RISK_BUDGET_BELOW_MINIMUM_VOLUME')
    report['risk_rejection_count'] = risk_gates.count()
    report['risk_rejections'] = recent(risk_gates, 1000, '-observed_at',
        'observed_at','reason','observation_id','details')
    report['risk_rejections_truncated'] = report['risk_rejection_count'] > 1000
    report['entry_signals'] = recent(obs.filter(action__in=['BUY','SELL']), 1000,
        '-recorded_at','id','recorded_at','signal_bar_time','action','reason','snapshot')
    report['latest_commands'] = recent(commands, 200, '-created_at',
        'command_id','action','state','created_at','expires_at','claimed_at',
        'completed_at','execution_authorized','payload','result')
    report['market_bars'] = {}
    for tf in ('M1','M5'):
        bars = MarketBar.objects.filter(source=feed.instance_id, symbol=feed.symbol, timeframe=tf)
        report['market_bars'][tf] = recent(bars, 100, '-timestamp',
            'timestamp','received_at','open','high','low','close','volume')
    report['market_data_health'] = {}
    for tf, seconds in (('M1', 60), ('M5', 300)):
        rows = report['market_bars'][tf]
        report['market_data_health'][tf] = dict(
            latest_closed_bar_age_seconds=(now - rows[0]['timestamp']).total_seconds() - seconds if rows else None,
            latest_receipt_age_seconds=(now - rows[0]['received_at']).total_seconds() if rows else None,
            sampled_gap_count=sum((a['timestamp']-b['timestamp']).total_seconds() != seconds
                                  for a,b in zip(rows, rows[1:])),
            sample_count=len(rows))
    return report


@staff_member_required
@require_GET
def download(request):
    now = timezone.now()
    response = JsonResponse(build_report(now), json_dumps_params={'indent': 2})
    response['Content-Disposition'] = 'attachment; filename="ramon-fast-scalp-diagnostic-%s.json"' % now.strftime('%Y%m%d-%H%M%S-UTC')
    response['Cache-Control'] = 'no-store'
    return response
