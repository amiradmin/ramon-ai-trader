"""Display-only forecasts. This module never queues orders or changes policy."""
import json
import math
import time
from pathlib import Path


def finite(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def forecast_advisor(name, median, reference, band, *, captured, horizon=None):
    median, reference, band = finite(median), finite(reference), finite(band)
    result = {"name": name, "status": "UNAVAILABLE", "direction": None,
              "captured": captured, "horizon_bars": horizon, "display_only": True}
    if median is None or reference is None or band is None or min(median, reference) <= 0 or band < 0:
        return result
    result.update(status="READY", forecast_price=median, reference_price=reference,
                  direction="BUY" if median > reference + band else "SELL" if median < reference - band else "WAIT")
    return result


def snapshot_advisors(audit, mid, spread, captured):
    base = audit.get("base") or {}
    final = audit.get("final") or {}
    settings = audit.get("settings") or {}
    result = [forecast_advisor("Chronos", base.get("forecast_median"), mid,
                               max(0.0, finite(spread) or 0.0) / 2,
                               captured=captured, horizon=settings.get("horizon"))]
    result[0]["model"] = (audit.get("handlers") or {}).get("forecast_model_handler") or "Chronos (Ramon)"
    experimental = audit.get("experimental_models") or audit.get("shadow_forecasts") or {}
    tf = experimental.get("timesfm3") or {}
    ready = tf.get("timesfm3_experimental_ready", tf.get("timesfm3_shadow_ready")) == 1
    item = forecast_advisor("TimesFM", tf.get("timesfm3_experimental_median", tf.get("timesfm3_shadow_median")) if ready else None,
                            mid, max(0.0, finite(spread) or 0.0) / 2, captured=captured, horizon=settings.get("horizon"))
    result.append(item)
    return result


def kronos_advisor(db, symbol, signal_bar, *, now=None):
    now = time.time() if now is None else now
    result = {"name": "Kronos-small", "model": "NeoQuasar/Kronos-small",
              "status": "UNAVAILABLE", "direction": None, "display_only": True}
    try:
        path = Path(db).with_name("kronos_live_advice.json")
        if path.stat().st_size > 16384:
            return result
        data = json.loads(path.read_text())
        generated = finite(data.get("generated"))
        bar = finite(data.get("signal_bar_time"))
        if (data.get("mode") != "DISPLAY_ONLY" or data.get("model") != result["model"]
                or data.get("symbol") != symbol or generated is None or bar is None):
            return result
        source_bar = bar
        if data.get("clock") == "UTC_FROM_LIVE_REQUEST":
            offset = data.get("broker_utc_offset_seconds")
            source_bar = finite(data.get("source_signal_bar_time"))
            if (type(offset) is not int or abs(offset) > 14 * 3600 or offset % 900
                    or source_bar is None or source_bar - offset != bar):
                return result
        if (int(source_bar) != int(signal_bar) or int(bar) != int(now // 900) * 900 - 900
                or generated < bar + 900 or not 0 <= now - generated <= 900):
            return dict(result, status="STALE")
        horizon = data.get("horizon_bars")
        if not isinstance(horizon, int) or not 1 <= horizon <= 16:
            return result
        result = forecast_advisor(result["name"], data.get("forecast_price"), data.get("reference_price"),
                                   data.get("neutral_band"), captured=generated, horizon=horizon)
        result["model"] = "NeoQuasar/Kronos-small"
        result["signal_bar_time"] = int(bar)
        return result
    except (OSError, ValueError, TypeError, AttributeError):
        return result
