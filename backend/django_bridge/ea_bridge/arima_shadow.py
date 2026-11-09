"""Forward-only ARIMA observation and scoring. No decision or order writes."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
import hashlib
import json
import logging
import math

from django.conf import settings
from django.utils import timezone

from .models import ArimaShadowForecast, EAHeartbeat, MarketBar

log = logging.getLogger(__name__)
INTERVAL = timedelta(minutes=15)
MIN_OBSERVATIONS = 128
MAX_ISSUE_DELAY = timedelta(minutes=2)


def model_configuration():
    window = settings.RAMON_ARIMA_SHADOW_WINDOW
    if not 128 <= window <= 1000:
        raise ValueError("RAMON_ARIMA_SHADOW_WINDOW must be between 128 and 1000")
    return window, f"arima-111-m15-w{window}-v1"


def demo_feeds(*, fresh_only=False, now=None):
    now = now or timezone.now()
    rows = EAHeartbeat.objects.exclude(symbol="").order_by("-last_seen")
    if fresh_only:
        rows = rows.filter(last_seen__gte=now-timedelta(minutes=5), last_seen__lte=now,
                           terminal_connected=True, status="TELEMETRY_OK")
    return [row for row in rows[:100] if isinstance(row.payload, dict)
            and type(row.payload.get("account_trade_mode")) is int
            and row.payload["account_trade_mode"] == 0]


def _market_rows(source, symbol, now):
    # Never mix independent EA feeds or use forming, future, or not-yet-received bars.
    return MarketBar.objects.filter(source=source, symbol=symbol, timeframe="M15",
        timestamp__lte=now-INTERVAL, received_at__lte=now, timestamp__second=0,
        timestamp__minute__in=(0, 15, 30, 45)).order_by("-timestamp")


def _load_history(source, symbol, now, limit):
    rows = []
    for row in _market_rows(source, symbol, now).iterator(chunk_size=1000):
        if row.timestamp.microsecond:
            continue
        rows.append(row)
        if len(rows) >= limit:
            break
    rows.reverse()
    return rows


def _settle_forecasts(now):
    settled = 0
    due = ArimaShadowForecast.objects.filter(status=ArimaShadowForecast.Status.READY,
                                             target_close_time__lte=now).order_by("target_close_time")[:200]
    for row in due:
        if row.generated_at is None or row.generated_at >= row.target_close_time:
            status, reason, actual = ArimaShadowForecast.Status.SKIPPED, "LATE_FORECAST", None
        else:
            bar = MarketBar.objects.filter(source=row.source, symbol=row.symbol, timeframe=row.timeframe,
                                          timestamp=row.target_bar_time, received_at__lte=now).first()
            if bar is None:
                if now < row.target_close_time+timedelta(hours=1):
                    continue
                status, reason, actual = ArimaShadowForecast.Status.SKIPPED, "TARGET_CANDLE_NOT_RECEIVED", None
            elif not bar.close.is_finite() or bar.close <= 0:
                status, reason, actual = ArimaShadowForecast.Status.SKIPPED, "INVALID_ACTUAL_CLOSE", None
            else:
                status, reason, actual = ArimaShadowForecast.Status.SCORED, "FORWARD_OUTCOME_RECORDED", bar.close
        settled += ArimaShadowForecast.objects.filter(pk=row.pk, status=ArimaShadowForecast.Status.READY).update(
            status=status, reason=reason, actual_close=actual, settled_at=now)
    return settled


def _publish_for_feed(feed, now, window, version):
    bars = _load_history(feed.instance_id, feed.symbol, now, window)
    if not bars:
        return False
    origin = bars[-1]
    row, created = ArimaShadowForecast.objects.get_or_create(
        source=feed.instance_id, symbol=feed.symbol, timeframe="M15", model_version=version,
        origin_bar_time=origin.timestamp, defaults={
            "target_bar_time": origin.timestamp+INTERVAL,
            "target_close_time": origin.timestamp+2*INTERVAL,
            "origin_close": origin.close, "started_at": now, "observations": len(bars),
        })
    if not created:
        return False  # one attempt per origin/version, including concurrent workers or restarts
    diagnostics = {"order": [1, 1, 1], "window": window,
                   "series": "COMPLETED_OBSERVED_BARS_NO_INTERPOLATION"}
    reason = ""
    if now-(origin.timestamp+INTERVAL) > MAX_ISSUE_DELAY:
        reason = "WAITING_FOR_NEXT_CANDLE"
    elif len(bars) < MIN_OBSERVATIONS:
        reason = "INSUFFICIENT_CANDLE_HISTORY"
    elif any(not bar.close.is_finite() or bar.close <= 0 for bar in bars):
        reason = "INVALID_CANDLE_CLOSE"
    if reason:
        ArimaShadowForecast.objects.filter(pk=row.pk).update(
            status=ArimaShadowForecast.Status.SKIPPED, reason=reason, diagnostics=diagnostics)
        return True
    missing = sum(max(0, int((b.timestamp-a.timestamp).total_seconds()/900)-1) for a, b in zip(bars, bars[1:]))
    diagnostics["missing_clock_intervals"] = missing
    fingerprint = hashlib.sha256(json.dumps(
        [(bar.timestamp.isoformat(), str(bar.close)) for bar in bars], separators=(",", ":")
    ).encode()).hexdigest()
    try:
        # Only the background worker imports/fits statsmodels; dashboard GETs never fit.
        from ramon_pipeline.signalflow.forecasting.arima import forecast_next_close
        prediction = forecast_next_close([float(bar.close) for bar in bars])
        generated_at = timezone.now()
        status = ArimaShadowForecast.Status.READY
        reason = "FORECAST_ISSUED_BEFORE_OUTCOME"
        if generated_at >= row.target_close_time or generated_at-(origin.timestamp+INTERVAL) > MAX_ISSUE_DELAY:
            status, reason = ArimaShadowForecast.Status.SKIPPED, "MODEL_FINISHED_TOO_LATE"
        values = {key: Decimal(str(prediction[key])) for key in ("forecast_close", "lower_95", "upper_95")}
        if not all(value.is_finite() and 0 < value < Decimal("1e14") for value in values.values()):
            raise ValueError("MODEL_OUTPUT_OUT_OF_RANGE")
        if not values["lower_95"] <= values["forecast_close"] <= values["upper_95"]:
            raise ValueError("MODEL_INTERVAL_INVALID")
        diagnostics["warning_count"] = prediction["warning_count"]
        ArimaShadowForecast.objects.filter(pk=row.pk, status=ArimaShadowForecast.Status.RUNNING).update(
            **values, generated_at=generated_at, status=status, reason=reason,
            diagnostics=diagnostics, input_sha256=fingerprint)
    except Exception as exc:
        log.exception("ARIMA observer failed for forecast %s", row.pk)
        diagnostics["error_type"] = type(exc).__name__
        reason = "MODEL_DID_NOT_CONVERGE" if str(exc) == "MODEL_DID_NOT_CONVERGE" else "MODEL_ERROR"
        ArimaShadowForecast.objects.filter(pk=row.pk, status=ArimaShadowForecast.Status.RUNNING).update(
            status=ArimaShadowForecast.Status.FAILED, reason=reason, diagnostics=diagnostics,
            input_sha256=fingerprint)
    return True


def process_forecast_cycle(*, now=None):
    """Score existing predictions and issue at most one forecast per current demo feed."""
    if not settings.RAMON_ARIMA_SHADOW_ENABLED:
        return {"processed": 0, "settled": 0}
    now = now or timezone.now()
    window, version = model_configuration()
    ArimaShadowForecast.objects.filter(status=ArimaShadowForecast.Status.RUNNING,
        started_at__lt=now-MAX_ISSUE_DELAY).update(status=ArimaShadowForecast.Status.FAILED,
                                                 reason="WORKER_INTERRUPTED_OR_TIMED_OUT")
    settled = _settle_forecasts(now)
    processed = 0
    for feed in demo_feeds(fresh_only=True, now=now):
        processed += _publish_for_feed(feed, now, window, version)
    return {"processed": processed, "settled": settled}


def accuracy_summary(rows):
    """Metrics from genuinely forward-scored predictions, against persistence."""
    count = len(rows)
    result = {"observations": count, "mae": None, "rmse": None, "baseline_mae": None,
              "baseline_rmse": None, "directional_accuracy": None, "interval_coverage": None,
              "comparison": "COLLECTING_RESULTS", "minimum_comparison_observations": 20}
    if not count:
        return result
    errors = [float(row.forecast_close-row.actual_close) for row in rows]
    baseline = [float(row.origin_close-row.actual_close) for row in rows]
    sign = lambda value: (value > 0)-(value < 0)
    result.update(
        mae=sum(abs(value) for value in errors)/count,
        rmse=math.sqrt(sum(value*value for value in errors)/count),
        baseline_mae=sum(abs(value) for value in baseline)/count,
        baseline_rmse=math.sqrt(sum(value*value for value in baseline)/count),
        directional_accuracy=sum(sign(row.forecast_close-row.origin_close) ==
                                 sign(row.actual_close-row.origin_close) for row in rows)/count,
        interval_coverage=sum(row.lower_95 <= row.actual_close <= row.upper_95 for row in rows)/count,
    )
    if count >= 20:
        result["comparison"] = "BEATS_BASELINE" if (result["mae"] < result["baseline_mae"]
            and result["rmse"] < result["baseline_rmse"]) else "DOES_NOT_BEAT_BASELINE"
    return result
