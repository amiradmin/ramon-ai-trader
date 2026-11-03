"""Assisted-learning API and read-only dashboard for Ramon."""
import hashlib
import json
import math
from datetime import datetime
from functools import wraps

from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .auth import BearerTokenRequired
from .models import EACommand, LearningRecommendation, LearningTradeOutcome, OrderIntentRecord
from .learning_store import DjangoOutcomeStore
from ramon_pipeline.learning import LearningEngine, RecommendationEngine


def _body(request):
    from django.conf import settings
    if len(request.body) > getattr(settings, "EA_MAX_BODY_BYTES", 65536):
        raise ValueError("request body too large")
    value = json.loads(request.body or b"{}")
    if not isinstance(value, dict):
        raise ValueError("JSON object required")
    return value


def _learning():
    return LearningEngine(store=DjangoOutcomeStore())


def _staff_api(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated or not request.user.is_active or not request.user.is_staff:
            return JsonResponse({"ok": False, "error": "Django staff authentication required"}, status=403)
        return view(request, *args, **kwargs)
    return wrapped


def _recommendation_dict(row):
    return {
        "id": row.id, "finding_code": row.finding_code, "severity": row.severity,
        "title": row.title, "detail": row.detail, "evidence": row.evidence,
        "status": row.status, "reviewer": row.reviewer, "review_note": row.review_note,
        "created_at": row.created_at.isoformat(),
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
        "automatic_application": False,
    }


def _execution_diagnostics():
    rows = list(LearningTradeOutcome.objects.order_by("-closed_at")[:500])
    fields = ("mae", "mfe", "entry_efficiency", "exit_efficiency", "spread_points", "slippage_points")
    result = {"sample_trades": len(rows), "metrics": {}}
    for field in fields:
        values = []
        for row in rows:
            value = (row.execution_metrics or {}).get(field)
            if isinstance(value, (int, float)) and math.isfinite(value):
                values.append(float(value))
        result["metrics"][field] = {
            "observations": len(values),
            "average": round(sum(values) / len(values), 6) if values else None,
        }
    return result


@require_GET
def learning_dashboard(request):
    return render(request, "ea_bridge/learning_dashboard.html")


@require_GET
def learning_overview(request):
    engine = _learning()
    summary = engine.summary()
    review = engine.review(min_samples=30)
    recent = []
    for row in LearningTradeOutcome.objects.order_by("-closed_at", "-received_at")[:25]:
        recent.append({
            "trade_id": row.trade_id, "decision_id": row.decision_id, "symbol": row.symbol,
            "realized_pnl": float(row.realized_pnl),
            "r_multiple": float(row.r_multiple) if row.r_multiple is not None else None,
            "closed_at": row.closed_at.isoformat() if row.closed_at else None,
            "tags": row.tags, "decision": row.decision_snapshot,
            "execution_metrics": row.execution_metrics,
        })
    return JsonResponse({
        "ok": True, "mode": "ASSISTED", "auto_apply": False,
        "strategy_mutation_enabled": False,
        "summary": summary, "review": review,
        "analysis": engine.analyze(min_samples=30),
        "execution_diagnostics": _execution_diagnostics(),
        "recommendations": [_recommendation_dict(row) for row in LearningRecommendation.objects.all()[:50]],
        "recent_outcomes": recent,
        "data_quality": {
            "closed_outcomes": LearningTradeOutcome.objects.filter(closed_at__isnull=False).count(),
            "outcomes_without_decision_link": LearningTradeOutcome.objects.filter(decision_id="").count(),
            "note": "Realized PnL must be net of broker commission, swap and spread. Missing execution metrics remain unavailable, not zero.",
        },
    })


@csrf_exempt
@require_POST
def record_outcome(request):
    denied = BearerTokenRequired.reject_if_invalid(request)
    if denied:
        return denied
    try:
        payload = _body(request)
        trade_id = str(payload.get("trade_id") or payload.get("deal_ticket") or "").strip()
        if not trade_id:
            raise ValueError("trade_id or deal_ticket is required")
        if payload.get("closed_at") is None:
            raise ValueError("closed_at is required for a completed trade outcome")
        for field in ("opened_at", "closed_at"):
            if payload.get(field) is not None:
                stamp = payload[field]
                if not isinstance(stamp, str):
                    raise ValueError(f"{field} must be a timezone-aware ISO-8601 string")
                parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                if parsed.tzinfo is None or parsed.utcoffset() is None:
                    raise ValueError(f"{field} must include a timezone")
        decision_id = str(payload.get("decision_id") or "").strip()
        instance_id = str(payload.get("instance_id") or "").strip()
        account_login = str(payload.get("account_login") or "").strip()
        if not decision_id and payload.get("command_id"):
            commands = EACommand.objects.filter(command_id=str(payload["command_id"]))
            if instance_id:
                commands = commands.filter(instance_id=instance_id)
            if account_login:
                commands = commands.filter(account_login=account_login)
            command = commands.first()
            if command:
                decision_id = command.decision_id
        if not decision_id and payload.get("broker_ticket"):
            # MT5/brokers may truncate the RamonCmd comment. The opening order
            # ticket is also returned in the command acknowledgement, so use it
            # as the durable, account-scoped attribution key.
            commands = EACommand.objects.filter(
                result__broker_ticket=str(payload["broker_ticket"])
            ).order_by("-created_at")
            if instance_id:
                commands = commands.filter(instance_id=instance_id)
            if account_login:
                commands = commands.filter(account_login=account_login)
            command = commands.first()
            if command:
                decision_id = command.decision_id
            else:
                intents = OrderIntentRecord.objects.filter(
                    broker_ticket=str(payload["broker_ticket"])
                ).order_by("-created_at")
                if instance_id:
                    intents = intents.filter(instance_id=instance_id)
                intent = intents.first()
                if intent:
                    decision_id = intent.decision_id
        outcome = dict(payload)
        outcome["trade_id"] = trade_id[:128]
        if decision_id:
            outcome["decision_id"] = decision_id[:128]
        for key in ("symbol", "instance_id", "account_login"):
            if payload.get(key) is not None:
                outcome[key] = str(payload[key])[:128]
        result = _learning().observe(outcome)
        if not result.get("accepted"):
            status = 409 if result.get("reason") == "CONFLICTING_OUTCOME" else 400
            return JsonResponse(result, status=status)
        status = 200 if result.get("reason") == "DUPLICATE_OUTCOME_IGNORED" else 201
        return JsonResponse(result, status=status)
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return JsonResponse({"accepted": False, "reason": "INVALID_OUTCOME", "detail": str(exc)}, status=400)


@_staff_api
@require_POST
def generate_recommendations(request):
    review = _learning().review(min_samples=30)
    if not review.get("sufficient_data"):
        return JsonResponse({
            "ok": True, "created": 0, "sufficient_data": False,
            "message": "At least 30 closed outcomes are required before recommendations are generated.",
        })
    created = 0
    for recommendation in RecommendationEngine().generate(review):
        code = str(recommendation["finding_code"])[:96]
        evidence = recommendation["evidence"]
        fingerprint_source = json.dumps(
            {"code": code, "evidence": evidence}, sort_keys=True, separators=(",", ":"), default=str
        )
        fingerprint = hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()
        _, was_created = LearningRecommendation.objects.get_or_create(
            fingerprint=fingerprint,
            defaults={
                "finding_code": code,
                "severity": str(recommendation["severity"] or "INFO")[:16],
                "title": str(recommendation["title"]),
                "detail": str(recommendation["detail"]),
                "evidence": evidence,
            },
        )
        created += int(was_created)
    return JsonResponse({
        "ok": True, "created": created, "sufficient_data": True,
        "message": "Findings are advisory hypotheses. Acceptance authorizes a backtest only; it never changes trading parameters.",
    })


@_staff_api
@require_POST
def review_recommendation(request, recommendation_id):
    try:
        payload = _body(request)
        decision = str(payload.get("decision") or "").upper()
        note = str(payload.get("note") or "")[:2000]
        if decision not in {"ACCEPT", "REJECT"}:
            raise ValueError("decision must be ACCEPT or REJECT")
        row = LearningRecommendation.objects.get(pk=recommendation_id)
        if row.status != LearningRecommendation.Status.OPEN:
            return JsonResponse({"ok": False, "error": "recommendation has already been reviewed"}, status=409)
        row.status = (
            LearningRecommendation.Status.ACCEPTED_FOR_BACKTEST
            if decision == "ACCEPT" else LearningRecommendation.Status.REJECTED
        )
        row.reviewer = request.user.get_username()
        row.review_note = note
        row.reviewed_at = timezone.now()
        row.save(update_fields=["status", "reviewer", "review_note", "reviewed_at"])
        return JsonResponse({"ok": True, "recommendation": _recommendation_dict(row), "strategy_changed": False})
    except LearningRecommendation.DoesNotExist:
        return JsonResponse({"ok": False, "error": "recommendation not found"}, status=404)
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
