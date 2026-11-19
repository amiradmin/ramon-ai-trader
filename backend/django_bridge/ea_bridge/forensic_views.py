"""Read-only trade forensics for the Ramon research dashboard.

Never infers a command link from a shared decision key. Historical records
without authoritative command identity remain explicitly unattributed.
"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal, InvalidOperation

from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from .models import EACommand, LearningTradeOutcome


def _date_param(value, fallback):
    if not value:
        return fallback
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise ValueError("Dates must be ISO 8601 (YYYY-MM-DD or timezone-aware timestamp)") from None
    if result.tzinfo is None:
        if len(value) == 10:
            result = result.replace(tzinfo=dt_timezone.utc)
        else:
            raise ValueError("Timestamps must include a timezone")
    return result.astimezone(dt_timezone.utc)


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
        return float(number) if number.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _text(value, default="UNKNOWN"):
    return str(value).strip() if isinstance(value, str) and value.strip() else default


def _nested(obj, *path):
    for key in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def _group(records, key):
    buckets = defaultdict(list)
    for row in records:
        buckets[row[key]].append(row)
    result = []
    for label, rows in buckets.items():
        pnl = sum(row["pnl"] for row in rows)
        winners = sum(row["pnl"] > 0 for row in rows)
        gross_profit = sum(max(row["pnl"], 0) for row in rows)
        gross_loss = -sum(min(row["pnl"], 0) for row in rows)
        r_values = [row["r_multiple"] for row in rows if row["r_multiple"] is not None]
        result.append({
            "name": label, "trades": len(rows), "wins": winners,
            "win_rate": round(winners / len(rows) * 100, 2),
            "net_pnl": round(pnl, 2),
            "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss else None,
            "average_r": round(sum(r_values) / len(r_values), 3) if r_values else None,
        })
    return sorted(result, key=lambda row: (-row["trades"], row["name"]))


@require_GET
def forensic_dashboard(request):
    return render(request, "ea_bridge/trade_forensics.html")


@require_GET
def forensic_data(request):
    """Account-isolated, bounded diagnostics with no orders or strategy mutations."""
    now = timezone.now()
    try:
        start = _date_param(request.GET.get("start"), now - timedelta(days=7))
        end = _date_param(request.GET.get("end"), now)
        if start > end or end - start > timedelta(days=90):
            raise ValueError("Select an ascending date range of at most 90 days")
        limit = int(request.GET.get("limit", "250"))
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        symbol = request.GET.get("symbol", "").strip()
        account = request.GET.get("account_login", "").strip()
        if len(symbol) > 32 or len(account) > 32:
            raise ValueError("Invalid scope")
    except (ValueError, TypeError) as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)

    qs = LearningTradeOutcome.objects.filter(closed_at__gte=start, closed_at__lte=end)
    if symbol:
        qs = qs.filter(symbol=symbol)
    if account:
        qs = qs.filter(account_login=account)
    total = qs.count()
    outcomes = list(qs.order_by("-closed_at", "-pk")[:limit])
    # Only exact, explicitly persisted command IDs may establish attribution.
    ids = {_nested(outcome.tags, "command_id") for outcome in outcomes}
    ids.discard(None)
    ids.discard("")
    commands = {
        command.command_id: command
        for command in EACommand.objects.filter(command_id__in=ids, action=EACommand.Action.OPEN)
        .select_related("evaluation", "evaluation__verdict")
    }
    rows = []
    for outcome in outcomes:
        tags = outcome.tags if isinstance(outcome.tags, dict) else {}
        snapshot = outcome.decision_snapshot if isinstance(outcome.decision_snapshot, dict) else {}
        metrics = outcome.execution_metrics if isinstance(outcome.execution_metrics, dict) else {}
        command = commands.get(tags.get("command_id"))
        # The command and its evaluation must match the same source, account,
        # decision and symbol. No joining on timestamps, tickets alone or side.
        valid = bool(command and outcome.instance_id and outcome.account_login
            and command.instance_id == outcome.instance_id
            and command.account_login == outcome.account_login
            and command.symbol == outcome.symbol
            and command.decision_id and command.decision_id == outcome.decision_id
            and command.state == EACommand.State.EXECUTED)
        if not valid:
            command = None
        evaluation = command.evaluation if command else None
        if evaluation and (
            evaluation.instance_id != outcome.instance_id
            or evaluation.account_login != outcome.account_login
            or evaluation.decision_id != outcome.decision_id
            or evaluation.symbol != outcome.symbol
        ):
            evaluation = None
        features = evaluation.features if evaluation and isinstance(evaluation.features, dict) else {}
        stages = features.get("stages") if isinstance(features.get("stages"), dict) else {}
        opportunity = stages.get("opportunities") if isinstance(stages.get("opportunities"), dict) else {}
        regime = opportunity.get("regime") if isinstance(opportunity.get("regime"), dict) else {}
        setup = opportunity.get("setup") if isinstance(opportunity.get("setup"), dict) else {}
        market = features.get("market_snapshot") if isinstance(features.get("market_snapshot"), dict) else {}
        command_payload = command.payload if command and isinstance(command.payload, dict) else {}
        strategy = _text(command_payload.get("strategy_setup"), _text(tags.get("strategy_setup"),
                         _text(tags.get("setup_type"), "UNKNOWN")))
        side = _text(command_payload.get("side") if command else snapshot.get("action"),
                     _text(tags.get("side")))
        if side not in ("BUY", "SELL"):
            side = "UNKNOWN"
        # Regime tagged by outcome can still be reported without command proof,
        # but the label is distinguishable from verified evaluation metadata.
        regime_label = _text(regime.get("label"), _text(tags.get("regime")))
        risk = evaluation.verdict if evaluation else None
        reasons = []
        if not command:
            reasons.append("COMMAND_LINK_UNVERIFIED")
        if not evaluation:
            reasons.append("EVALUATION_NOT_LINKED")
        if not risk:
            reasons.append("RISK_VERDICT_UNAVAILABLE")
        if regime_label == "UNKNOWN":
            reasons.append("REGIME_UNAVAILABLE")
        pnl = float(outcome.realized_pnl)
        rows.append({
            "trade_id": outcome.trade_id,
            "closed_at": outcome.closed_at.isoformat(),
            "opened_at": outcome.opened_at.isoformat() if outcome.opened_at else None,
            "symbol": outcome.symbol, "side": side, "strategy": strategy,
            "regime": regime_label, "pnl": pnl,
            "r_multiple": _number(outcome.r_multiple),
            "command_id": command.command_id if command else None,
            "evaluation_id": evaluation.pk if evaluation else None,
            "decision_id": outcome.decision_id or None,
            "confidence": _number(evaluation.confidence) if evaluation else None,
            "decision_reason": evaluation.rationale_code if evaluation else None,
            "risk_reason": risk.reason_code if risk else None,
            "risk_approved": risk.approved if risk else None,
            "setup": _text(setup.get("type"), _text(tags.get("setup_type"))),
            "adx": _number(regime.get("adx")),
            "ema_bias": _text(regime.get("ema_bias"), "UNAVAILABLE"),
            "directional_efficiency": _number(regime.get("directional_efficiency")),
            "bid": _number(market.get("bid")), "ask": _number(market.get("ask")),
            "spread_points": _number(metrics.get("spread_points")),
            "slippage_points": _number(metrics.get("slippage_points")),
            "mae": _number(metrics.get("mae")), "mfe": _number(metrics.get("mfe")),
            "exit_reason": _text(tags.get("exit_reason"), "UNAVAILABLE"),
            "evidence_status": "VERIFIED_EVALUATION" if evaluation else ("LINKED_COMMAND_ONLY" if command else "UNLINKED"),
            "missing_evidence": reasons,
        })

    wins = sum(row["pnl"] > 0 for row in rows)
    losses = sum(row["pnl"] < 0 for row in rows)
    net = sum(row["pnl"] for row in rows)
    return JsonResponse({
        "ok": True, "read_only": True, "auto_apply": False,
        "scope": {"start": start.isoformat(), "end": end.isoformat(),
                  "symbol": symbol, "account_login": account},
        "sample": {"total_matching": total, "returned": len(rows),
                   "truncated": total > len(rows), "max_rows": limit},
        "summary": {"trades": len(rows), "wins": wins, "losses": losses,
                    "win_rate": round(wins / len(rows) * 100, 2) if rows else None,
                    "net_pnl": round(net, 2),
                    "linked_evaluations": sum(r["evaluation_id"] is not None for r in rows)},
        "by_direction": _group(rows, "side"),
        "by_strategy": _group(rows, "strategy"),
        "by_regime": _group(rows, "regime"),
        "trades": rows,
        "note": ("Descriptive metrics over the returned sample only; missing evidence is not inferred. "
                 "Ramon does not automatically retrain, change entry rules or issue commands here. "
                 "Attribution requires an exact account-scoped executed OPEN command."),
    })
