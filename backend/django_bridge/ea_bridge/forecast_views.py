"""Read-only dashboard feed for the independently running ARIMA observer."""
from django.conf import settings
from django.db.models import Count, F
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

from .arima_shadow import MAX_ISSUE_DELAY, accuracy_summary, demo_feeds, model_configuration
from .models import ArimaShadowForecast, StrategyDecisionRecord


def _forecast_dict(row):
    number = lambda value: None if value is None else float(value)
    direction = None
    interval_direction = None
    if row.forecast_close is not None:
        direction = "UP" if row.forecast_close > row.origin_close else "DOWN" if row.forecast_close < row.origin_close else "FLAT"
        interval_direction = "UP" if row.lower_95 > row.origin_close else "DOWN" if row.upper_95 < row.origin_close else "UNCERTAIN"
    return {
        "status": row.status, "reason": row.reason, "origin_bar_time": row.origin_bar_time.isoformat(),
        "target_bar_time": row.target_bar_time.isoformat(), "target_close_time": row.target_close_time.isoformat(),
        "generated_at": row.generated_at.isoformat() if row.generated_at else None,
        "origin_close": number(row.origin_close), "forecast_close": number(row.forecast_close),
        "lower_95": number(row.lower_95), "upper_95": number(row.upper_95),
        "actual_close": number(row.actual_close), "direction": direction, "interval_direction": interval_direction,
        "observations": row.observations, "diagnostics": row.diagnostics,
        "absolute_error": number(abs(row.forecast_close-row.actual_close)) if row.actual_close is not None else None,
        "baseline_absolute_error": number(abs(row.origin_close-row.actual_close)) if row.actual_close is not None else None,
    }


@require_GET
def arima_status(request):
    now = timezone.now()
    result = {"ok": True, "mode": "OBSERVATION_ONLY", "affects_orders": False,
              "enabled": settings.RAMON_ARIMA_SHADOW_ENABLED, "model": "ARIMA(1,1,1)",
              "timeframe": "M15", "server_time": now.isoformat(), "latest": None,
              "recent": [], "accuracy": accuracy_summary([]), "alignment": "NO_ACTIVE_FORECAST"}
    if not result["enabled"]:
        return JsonResponse({**result, "state": "DISABLED"})
    _, version = model_configuration()
    result["model_version"] = version
    feeds = demo_feeds(now=now)
    if not feeds:
        return JsonResponse({**result, "state": "NO_DEMO_FEED"})
    feed = feeds[0]
    age = (now-feed.last_seen).total_seconds()
    result.update(source=feed.instance_id, symbol=feed.symbol, feed_age_seconds=max(0, round(age)))
    rows = ArimaShadowForecast.objects.filter(source=feed.instance_id, symbol=feed.symbol,
                                              timeframe="M15", model_version=version)
    recent = list(rows.order_by("-origin_bar_time")[:8])
    scored = list(rows.filter(status=ArimaShadowForecast.Status.SCORED,
        generated_at__gte=F("target_bar_time"), generated_at__lte=F("target_bar_time")+MAX_ISSUE_DELAY,
        generated_at__lt=F("target_close_time"), actual_close__isnull=False,
        forecast_close__isnull=False, lower_95__isnull=False, upper_95__isnull=False,
        observations__gte=128,
    ).order_by("-target_close_time")[:100])
    result["accuracy"] = accuracy_summary(scored)
    result["counts"] = {item["status"]: item["n"] for item in rows.values("status").annotate(n=Count("id"))}
    result["recent"] = [_forecast_dict(row) for row in recent]
    if not recent:
        return JsonResponse({**result, "state": "WAITING_FOR_FIRST_FORECAST"})
    latest = recent[0]
    result["latest"] = _forecast_dict(latest)
    active = latest.status == ArimaShadowForecast.Status.READY and now < latest.target_close_time
    if age < 0 or age > 300 or not feed.terminal_connected:
        result["state"] = "FEED_STALE"
    elif active:
        result["state"] = "FORECAST_ACTIVE"
    elif latest.status == ArimaShadowForecast.Status.READY:
        result["state"] = "WAITING_FOR_ACTUAL_CLOSE"
    else:
        result["state"] = latest.status
    if active and result["state"] == "FORECAST_ACTIVE":
        decision = StrategyDecisionRecord.objects.filter(
            symbol=feed.symbol, features__market_snapshot__source=feed.instance_id,
        ).order_by("-created_at").first()
        if decision is None or decision.action not in {"BUY", "SELL"}:
            result["alignment"] = "NO_APPROVED_TRADE"
        elif decision.features.get("market_snapshot", {}).get("bar_time") != latest.origin_bar_time.timestamp():
            result["alignment"] = "DIFFERENT_SIGNAL_CANDLE"
        else:
            expected = "UP" if decision.action == "BUY" else "DOWN"
            result["alignment"] = "ALIGNS_WITH_TRADE" if result["latest"]["direction"] == expected else "OPPOSES_TRADE"
    response = JsonResponse(result, json_dumps_params={"allow_nan": False})
    response["Cache-Control"] = "no-store"
    return response
