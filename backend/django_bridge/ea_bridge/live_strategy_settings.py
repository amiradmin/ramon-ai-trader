"""Account-scoped live M15 strategy controls. Never modifies broker specifications."""
import hashlib
import json
import math
import uuid

from django.contrib.admin.views.decorators import staff_member_required
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .models import DecisionThresholdConfig, StrategyWorkerControl, EAEvent
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
}
LABELS = {
    "minimum_score": "Minimum opportunity score",
    "minimum_quality": "Minimum entry quality",
    "maximum_spread_points": "Maximum allowed spread (points)",
    "maximum_risk_percent": "Maximum risk per entry (%)",
}


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
    scope_valid = bool(feed and feed.status == "TELEMETRY_OK"
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
            if not isinstance(proposed, dict) or set(proposed) != set(EDITABLE):
                raise ValueError("Provide exactly the four supported strategy settings")
            values = {}
            for key, (group, field) in EDITABLE.items():
                raw = proposed[key]
                if isinstance(raw, bool):
                    raise ValueError(key + " must be a number")
                value = float(raw)
                spec = THRESHOLDS[group][field]
                if not math.isfinite(value) or not spec["min"] <= value <= spec["max"]:
                    raise ValueError(key + " outside allowed range")
                values[key] = value
            # Live-account risk is only allowed to tighten the existing server
            # policy; it cannot increase the shared configured risk budget.
            current_ceiling = min(0.5, float(base["risk_management"]["maximum_risk_percent"]))
            if values["maximum_risk_percent"] > current_ceiling:
                raise ValueError("Live risk cannot exceed current shared/server limit")
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
                    "max": min(0.5, float(base["risk_management"]["maximum_risk_percent"]))
                           if key == "maximum_risk_percent" else THRESHOLDS[group][field]["max"],
                    "step": THRESHOLDS[group][field]["step"]}
                   for key, (group, field) in EDITABLE.items()],
    })
    response["Cache-Control"] = "no-store"
    return response
