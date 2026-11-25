"""Account-scoped live M15 strategy controls. Never modifies broker specifications."""
import hashlib
import json
import math
import uuid

from django.contrib.admin.views.decorators import staff_member_required
from decimal import Decimal, InvalidOperation
from django.conf import settings
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .models import DecisionThresholdConfig, StrategyWorkerControl, ScalperControl, EAHeartbeat, EAEvent
from .account_scope import real_cent_scope
from .scalp_policy import validated_policy, field_schema
from dataclasses import asdict
from .strategy_registry import STRATEGIES
from .strategy_worker import configured_feed
from .thresholds import THRESHOLDS, defaults_for

# Only explicit SignalFlow parameters are writable. Broker specs, account
# balance, lot limits, equity, account identity and execution gates are not.
EDITABLE = {
    "minimum_score": ("opportunities", "minimum_score"),
    "minimum_quality": ("entry_timing", "minimum_quality"),
    "maximum_spread_points": ("entry_timing", "maximum_spread_points"),
    "maximum_risk_percent": ("risk_management", "maximum_risk_percent"),
    "max_chase_atr": ("entry_timing", "max_chase_atr"),
    "max_signal_age_bars": ("entry_timing", "max_signal_age_bars"),
    "max_total_risk_percent": ("risk_management", "max_total_risk_percent"),
}
LABELS = {
    "minimum_score": "Minimum opportunity score",
    "minimum_quality": "Minimum entry quality",
    "maximum_spread_points": "Maximum allowed spread (points)",
    "maximum_risk_percent": "Maximum risk per entry (%)",
    "max_chase_atr": "Maximum chase ATR",
    "max_signal_age_bars": "Maximum signal age (bars)",
    "max_total_risk_percent": "Maximum portfolio risk (%)",
}


# Additional per-strategy settings use the existing validated threshold schema.
_EXTRA_FIELDS = {
    "entry_timing": ("min_risk_room_atr", "max_spread_atr", "max_spread_to_risk"),
    "risk_management": ("stop_buffer_atr", "min_stop_atr", "max_stop_atr",
                        "target_reward_risk", "min_reward_risk"),
}
for _group, _fields in _EXTRA_FIELDS.items():
    for _field in _fields:
        EDITABLE[_field] = (_group, _field)
        LABELS[_field] = THRESHOLDS[_group][_field]["label"]


def live_risk_ceiling(setup, base):
    """Only TREND_PULLBACK may explicitly opt into the higher live ceiling."""
    cap = 1.5 if setup == "TREND_PULLBACK" else min(0.5, float(base["risk_management"]["maximum_risk_percent"]))
    return min(cap, float(base["risk_management"]["max_total_risk_percent"]))


def profile_key(setup):
    if setup not in STRATEGIES:
        raise ValueError("Unknown setup")
    return "l" + hashlib.sha256(setup.encode("ascii")).hexdigest()[:15]


def scope_for(control):
    return [control.source, control.account_login, control.server_name, control.symbol]


def active_settings(setup, scope):
    """Read only exact-account overrides; never apply to a simulator account."""
    if not scope or setup not in STRATEGIES:
        return {}
    control = StrategyWorkerControl.objects.filter(setup=setup).first()
    if not control or list(scope) != scope_for(control):
        return {}
    row = DecisionThresholdConfig.objects.filter(profile=profile_key(setup)).first()
    data = row.values if row and isinstance(row.values, dict) else {}
    if data.get("scope") != scope_for(control):
        return {}
    return {k: v for k, v in data.get("overrides", {}).items() if k in EDITABLE}


def _effective_base():
    base = defaults_for("standard")
    shared = DecisionThresholdConfig.objects.filter(profile="standard").first()
    if shared and isinstance(shared.values, dict):
        for group, updates in shared.values.items():
            if group in base and isinstance(updates, dict):
                base[group].update({k: v for k, v in updates.items() if k in base[group]})
    return base


@staff_member_required
@require_http_methods(["GET", "POST"])
def live_strategy_settings(request):
    feed = configured_feed()
    controls = {c.setup: c for c in StrategyWorkerControl.objects.all()}
    scope_valid = bool(feed and real_cent_scope(feed)[0] and feed.status == "TELEMETRY_OK"
        and feed.terminal_connected and 0 <= (timezone.now() - feed.last_seen).total_seconds() <= 60)
    base = _effective_base()

    if request.method == "POST":
        try:
            body = json.loads(request.body)
            if not isinstance(body, dict):
                raise ValueError("JSON object required")
            setup = body.get("setup")
            if setup not in STRATEGIES:
                raise ValueError("Unknown strategy")
            control = controls.get(setup)
            if not control or not scope_valid or scope_for(control) != [
                feed.instance_id, feed.account_login, feed.server_name, feed.symbol
            ]:
                return JsonResponse({"ok": False, "error": "Live account scope or telemetry is not ready"}, status=409)
            if body.get("scope") != scope_for(control):
                return JsonResponse({"ok": False, "error": "Account identity changed: reload page"}, status=409)
            proposed = body.get("overrides")
            legacy_keys = {"minimum_score", "minimum_quality", "maximum_spread_points", "maximum_risk_percent"}
            if not isinstance(proposed, dict) or not legacy_keys <= set(proposed) <= set(EDITABLE):
                raise ValueError("Provide the supported strategy settings")
            proposed = {**active_settings(setup, scope_for(control)), **proposed}
            values = {}
            for key, (group, field) in EDITABLE.items():
                raw = proposed.get(key, base[group][field])
                if isinstance(raw, bool):
                    raise ValueError(key + " must be a number")
                value = float(raw)
                spec = THRESHOLDS[group][field]
                if not math.isfinite(value) or not spec["min"] <= value <= spec["max"]:
                    raise ValueError(key + " outside allowed range")
                values[key] = value
            if values["min_stop_atr"] >= values["max_stop_atr"]:
                raise ValueError("Minimum stop distance must be below maximum stop distance")
            if values["target_reward_risk"] < values["min_reward_risk"]:
                raise ValueError("Target reward/risk must be at least the minimum reward/risk")
            if values["max_total_risk_percent"] > min(2.0, float(base["risk_management"]["max_total_risk_percent"])):
                raise ValueError("Portfolio risk cannot exceed the shared live ceiling")
            if values["maximum_risk_percent"] > values["max_total_risk_percent"]:
                raise ValueError("Entry risk cannot exceed portfolio risk")
            current_ceiling = live_risk_ceiling(setup, base)
            if values["maximum_risk_percent"] > current_ceiling:
                raise ValueError("Live risk cannot exceed the strategy/server ceiling")
            with transaction.atomic():
                control = StrategyWorkerControl.objects.select_for_update().get(setup=setup)
                if scope_for(control) != body["scope"]:
                    return JsonResponse({"ok": False, "error": "Account scope changed"}, status=409)
                row, _ = DecisionThresholdConfig.objects.select_for_update().get_or_create(profile=profile_key(setup))
                row.values = {"scope": scope_for(control), "overrides": values}
                row.save(update_fields=["values", "updated_at"])
                EAEvent.objects.create(
                    instance_id=control.source,
                    event_id="live-setting-" + uuid.uuid4().hex,
                    event_type="STRATEGY_SETTINGS",
                    payload={"setup": setup, "user": request.user.get_username(),
                             "scope": scope_for(control), "settings": values,
                             "note": "Future M15 evaluations only; no trade submitted"},
                )
            return JsonResponse({"ok": True, "setup": setup,
                                 "updated_at": row.updated_at.isoformat(), "overrides": values})
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)

    result = []
    for setup, (slug, description, _) in STRATEGIES.items():
        control = controls.get(setup)
        scope = scope_for(control) if control else None
        row = DecisionThresholdConfig.objects.filter(profile=profile_key(setup)).first()
        overrides = active_settings(setup, scope) if scope else {}
        result.append({
            "setup": setup, "description": description, "scope": scope,
            "overrides": {key: overrides.get(key, base[group][field])
                          for key, (group, field) in EDITABLE.items()},
            "updated_at": row.updated_at.isoformat() if row and overrides else None,
            "maximum_risk_percent_ceiling": live_risk_ceiling(setup, base),
            "ready": bool(scope_valid and control and scope == [
                feed.instance_id, feed.account_login, feed.server_name, feed.symbol]),
        })
    payload = feed.payload if feed and isinstance(feed.payload, dict) else {}
    response = JsonResponse({
        "ok": True, "strategies": result,
        "broker": {
            "fresh": scope_valid, "symbol": feed.symbol if feed else None,
            "account": {k: payload.get("account", {}).get(k) for k in
                        ("balance", "equity", "free_margin", "currency")
                        if isinstance(payload.get("account"), dict)},
            "symbol_spec": payload.get("symbol_info", {}),
        },
        "fields": [{"key": key, "label": LABELS[key],
                    "min": THRESHOLDS[group][field]["min"],
                    "max": live_risk_ceiling("TREND_PULLBACK", base)
                           if key == "maximum_risk_percent" else min(2.0, float(base["risk_management"]["max_total_risk_percent"]))
                           if key == "max_total_risk_percent" else THRESHOLDS[group][field]["max"],
                    "step": THRESHOLDS[group][field]["step"]}
                   for key, (group, field) in EDITABLE.items()],
    })
    response["Cache-Control"] = "no-store"
    return response


@staff_member_required
@require_http_methods(["GET", "POST"])
def live_scalper_risk(request):
    """Existing independent M1/M5 scalper budget, capped at the engine's 0.15%."""
    identity = {
        "instance_id": settings.RAMON_SCALP_INSTANCE,
        "account_login": settings.RAMON_SCALP_LOGIN,
        "server_name": settings.RAMON_SCALP_SERVER,
    }
    feed = EAHeartbeat.objects.filter(**identity).first()
    control = (ScalperControl.objects.filter(
        source=feed.instance_id, account_login=feed.account_login,
        server_name=feed.server_name, symbol=feed.symbol).first() if feed else None)
    scope = ([control.source, control.account_login, control.server_name, control.symbol]
             if control else None)
    fresh = bool(feed and control and real_cent_scope(feed)[0] and feed.terminal_connected and
                 feed.status == "TELEMETRY_OK" and
                 0 <= (timezone.now() - feed.last_seen).total_seconds() <= 60)
    if request.method == "POST":
        try:
            body = json.loads(request.body)
            if not isinstance(body, dict) or not scope or not fresh or body.get("scope") != scope:
                return JsonResponse({"ok": False, "error": "Scalper scope or telemetry unavailable"}, status=409)
            raw = body.get("risk_percent")
            if isinstance(raw, bool):
                raise ValueError("Invalid scalper risk")
            risk = Decimal(str(raw))
            if not risk.is_finite() or not Decimal("0.010") <= risk <= Decimal("0.150"):
                raise ValueError("Scalper risk must be between 0.010% and 0.150%")
            if risk.as_tuple().exponent < -3:
                raise ValueError("Maximum three decimal places")
            proposed = body.get("signal_overrides")
            with transaction.atomic():
                control = ScalperControl.objects.select_for_update().get(pk=control.pk)
                if [control.source, control.account_login, control.server_name, control.symbol] != scope:
                    return JsonResponse({"ok": False, "error": "Scalper scope changed"}, status=409)
                control.risk_percent = risk
                if proposed is not None:
                    control.signal_overrides = asdict(validated_policy(proposed))
                control.save(update_fields=["risk_percent", "signal_overrides", "updated_at"])
                EAEvent.objects.create(
                    instance_id=control.source,
                    event_id="scalp-setting-" + uuid.uuid4().hex,
                    event_type="SCALPER_SETTINGS",
                    payload={"user": request.user.get_username(), "scope": scope,
                             "risk_percent": str(risk), "signal_overrides": control.signal_overrides,
                             "note": "Future scalp entries only; no order submitted"},
                )
            return JsonResponse({"ok": True, "risk_percent": str(risk)})
        except (ValueError, TypeError, InvalidOperation, json.JSONDecodeError) as exc:
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    payload = feed.payload if feed and isinstance(feed.payload, dict) else {}
    account = payload.get("account")
    spec = payload.get("symbol_info")
    broker = {
        "fresh": fresh, "symbol": feed.symbol if feed else None,
        "account": {k: account.get(k) for k in ("balance", "equity", "free_margin", "currency")}
                   if isinstance(account, dict) else {},
        "symbol_spec": spec if isinstance(spec, dict) else {},
    }
    response = JsonResponse({"ok": True, "ready": fresh, "scope": scope,
                             "broker": broker,
                             "risk_percent": str(control.risk_percent) if control else None,
                             "maximum_risk_percent": "0.150",
                             "fields": field_schema(),
                             "signal_overrides": asdict(validated_policy(control.signal_overrides if control else {}))})
    response["Cache-Control"] = "no-store"
    return response
