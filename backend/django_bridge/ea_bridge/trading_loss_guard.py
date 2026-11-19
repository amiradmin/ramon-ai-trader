"""Account-scoped realized-loss circuit breaker for new OPEN orders only.

This is intentionally independent of strategy selection: one strategy must not
evade an account-level loss streak by changing its magic number. Closing and
protective orders are never routed through this gate.
"""
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .models import LearningTradeOutcome


def open_loss_guard_reason(*, instance_id, account_login, symbol, now=None):
    """Return a stable block reason, or None when trading may proceed.

    Only confirmed, closed outcomes count. A zero-P/L outcome breaks a losing
    streak, and the time-window expiry permits a new evaluation. Settings are
    intentionally server-side, not part of the strategy's editable thresholds.
    """
    if not getattr(settings, "RAMON_OPEN_LOSS_GUARD_ENABLED", True):
        return None
    threshold = getattr(settings, "RAMON_OPEN_LOSS_GUARD_STREAK", 3)
    hours = getattr(settings, "RAMON_OPEN_LOSS_GUARD_HOURS", 24)
    if type(threshold) is not int or threshold < 1 or type(hours) not in (int, float) or hours <= 0:
        return "OPEN_LOSS_GUARD_INVALID_CONFIG"
    if not instance_id or not account_login or not symbol:
        return "OPEN_LOSS_GUARD_SCOPE_MISSING"
    now = now or timezone.now()
    recent = LearningTradeOutcome.objects.filter(
        instance_id=instance_id,
        account_login=account_login,
        symbol=symbol,
        closed_at__gte=now - timedelta(hours=hours),
        closed_at__lte=now,
    ).order_by("-closed_at", "-pk")[:threshold]
    rows = list(recent)
    if len(rows) == threshold and all(row.realized_pnl < 0 for row in rows):
        return "OPEN_LOSS_STREAK_LIMIT_REACHED"
    return None
