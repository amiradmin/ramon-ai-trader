"""Staff-only, read-only diagnostics for the live Trend Pullback worker."""
from datetime import timedelta

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

from .models import StrategyWorkerControl, DecisionEvaluation, EACommand, MarketBar, AccountDailyBaseline
from .strategy_worker import configured_feed, strategy_readiness
from .live_strategy_settings import active_settings, scope_for, _effective_base, EDITABLE, live_risk_ceiling
from .scalper_diagnostics import counts, recent

SETUP = "TREND_PULLBACK"
FLAGS = ("RAMON_STRATEGY_CONTAINERS_ENABLED", "RAMON_EA_COMMAND_CREATION_ENABLED",
         "RAMON_EA_COMMAND_EXECUTION_ENABLED", "RAMON_REAL_EXECUTION_ENABLED")


def build_report(now=None):
    now = now or timezone.now()
    since = now - timedelta(days=7)
    report = dict(schema_version=1, strategy=SETUP, read_only=True, generated_at=now,
                  window_start=since, flags={k: bool(getattr(settings, k, False)) for k in FLAGS},
                  sample_limits={"evaluations": 500, "commands": 200, "bars_per_timeframe": 300})
    feed = configured_feed()
    control = StrategyWorkerControl.objects.filter(setup=SETUP).first()
    report["readiness"] = strategy_readiness(feed, now=now)
    if not feed:
        return report
    scope = [feed.instance_id, feed.account_login, feed.server_name, feed.symbol]
    report["scope"] = dict(zip(("source", "account_login", "server_name", "symbol"), scope))
    matches = bool(control and scope_for(control) == scope)
    report["control_scope_matches"] = matches
    report["control"] = (dict(automatic=control.automatic, state=control.state,
        last_seen=control.last_seen, latest=control.latest,
        age_seconds=(now-control.last_seen).total_seconds() if control.last_seen else None)
        if matches else None)
    payload = feed.payload if isinstance(feed.payload, dict) else {}
    report["heartbeat"] = dict(status=feed.status, last_seen=feed.last_seen,
        age_seconds=(now-feed.last_seen).total_seconds(), ea_version=feed.ea_version,
        terminal_connected=feed.terminal_connected, trade_allowed=feed.trade_allowed,
        telemetry={k: payload.get(k) for k in ("quote_time", "quote", "account",
            "symbol_info", "positions", "broker_day", "execution_profile", "account_margin_mode")})
    base = _effective_base()
    overrides = active_settings(SETUP, scope)
    report["risk_ceiling_percent"] = live_risk_ceiling(SETUP, base)
    report["saved_overrides"] = overrides
    for key, value in overrides.items():
        group, field = EDITABLE[key]
        base[group][field] = value
    report["configured_thresholds"] = base
    report["daily_baselines"] = list(AccountDailyBaseline.objects.filter(
        account_login=feed.account_login, server_name=feed.server_name
    ).order_by("-broker_day").values("broker_day", "opening_balance", "capture_method")[:8])
    evaluations = DecisionEvaluation.objects.filter(instance_id=feed.instance_id,
        account_login=feed.account_login, server_name=feed.server_name, symbol=feed.symbol,
        features__strategy_setup=SETUP, created_at__gte=since, created_at__lte=now)
    commands = EACommand.objects.filter(instance_id=feed.instance_id,
        account_login=feed.account_login, symbol=feed.symbol, payload__server_name=feed.server_name,
        payload__engine="STRATEGY", payload__strategy_setup=SETUP,
        created_at__gte=since, created_at__lte=now)
    report["signal_counts"] = counts(evaluations, "action", "rationale_code")
    report["risk_counts"] = counts(evaluations, "verdict__approved", "verdict__reason_code")
    report["command_counts"] = counts(commands, "action", "state")
    report["latest_evaluations"] = recent(evaluations, 500, "-created_at", "decision_id",
        "created_at", "action", "rationale_code", "confidence", "model_version", "features",
        "verdict__approved", "verdict__reason_code", "verdict__approved_volume",
        "verdict__stop_loss", "verdict__take_profit", "verdict__details")
    report["latest_commands"] = recent(commands, 200, "-created_at", "command_id",
        "decision_id", "created_at", "expires_at", "claimed_at", "completed_at",
        "action", "state", "execution_authorized", "payload", "result")
    report["market_bars"] = {tf: recent(MarketBar.objects.filter(source=feed.instance_id,
        symbol=feed.symbol, timeframe=tf, timestamp__lte=now), 300, "-timestamp",
        "timestamp", "received_at", "open", "high", "low", "close", "volume")
        for tf in ("M15", "H1", "H4")}
    return report


@staff_member_required
@require_GET
def download(request):
    now = timezone.now()
    response = JsonResponse(build_report(now), json_dumps_params={"indent": 2})
    response["Content-Disposition"] = 'attachment; filename="ramon-trend-pullback-diagnostic-%s.json"' % now.strftime("%Y%m%d-%H%M%S-UTC")
    response["Cache-Control"] = "no-store"
    return response
