"""Conservatively reconstruct missing R labels from Django's executed OPEN commands.

The command is dry-run by default. It never estimates risk from broker prices or
EA-supplied values: only a uniquely linked, executed, authorized OPEN command and
its approved Django risk verdict can supply the initial cash risk.
"""
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand
from django.db import transaction

from ea_bridge.models import (
    EACommand,
    LearningTradeOutcome,
    RiskVerdictRecord,
    StrategyDecisionRecord,
)


ZERO = Decimal("0")
TOLERANCE = Decimal("0.00000001")
R_QUANTUM = Decimal("0.00000001")


def _decimal(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _trusted_risk_for_outcome(row):
    """Return (risk, reason) only when one exact server-authorized link is proven."""
    if not row.decision_id or not row.instance_id or not row.account_login or not row.symbol:
        return None, "missing decision/account/instance/symbol identity"

    decision = StrategyDecisionRecord.objects.filter(
        decision_id=row.decision_id, symbol=row.symbol
    ).first()
    if decision is None or decision.action not in ("BUY", "SELL"):
        return None, "matching strategy decision not found"

    tags = row.tags if isinstance(row.tags, dict) else {}
    commands = EACommand.objects.filter(
        action=EACommand.Action.OPEN,
        state=EACommand.State.EXECUTED,
        execution_authorized=True,
        decision_id=row.decision_id,
        instance_id=row.instance_id,
        account_login=row.account_login,
        symbol=row.symbol,
    )
    command_id = str(tags.get("command_id") or "").strip()
    broker_ticket = str(tags.get("broker_ticket") or "").strip()
    if command_id:
        commands = commands.filter(command_id=command_id)
    if broker_ticket:
        commands = commands.filter(result__broker_ticket=broker_ticket)

    matches = list(commands.order_by("-completed_at", "-created_at")[:2])
    if len(matches) != 1:
        return None, "no unique executed OPEN command matches this outcome"

    command = matches[0]
    payload = command.payload if isinstance(command.payload, dict) else {}
    if str(payload.get("side") or "").upper() != decision.action:
        return None, "command side does not match the persisted decision"
    command_volume = _decimal(payload.get("volume"))
    if command_volume is None or command_volume <= ZERO:
        return None, "command volume is unavailable"

    verdict = RiskVerdictRecord.objects.filter(
        pk=command.risk_verdict_id,
        decision_id=row.decision_id,
        approved=True,
    ).first() if command.risk_verdict_id else None
    if verdict is None or verdict.approved_volume <= ZERO:
        return None, "approved risk verdict is unavailable"

    details = verdict.details if isinstance(verdict.details, dict) else {}
    stage = details.get("risk_stage")
    if not isinstance(stage, dict):
        return None, "approved risk stage is unavailable"
    risk_amount = _decimal(stage.get("estimated_risk"))
    stage_volume = _decimal(stage.get("volume"))
    if risk_amount is None or risk_amount <= ZERO:
        return None, "approved initial cash risk is invalid"
    if stage_volume is None or stage_volume <= ZERO:
        return None, "approved risk-stage volume is invalid"
    if (
        abs(stage_volume - verdict.approved_volume) > TOLERANCE
        or abs(command_volume - verdict.approved_volume) > TOLERANCE
    ):
        return None, "command, verdict, and risk-stage volumes do not agree"

    return risk_amount, "trusted executed command and approved risk verdict"


class Command(BaseCommand):
    help = "Dry-run or apply safe backfills for missing trade R-multiple labels."

    def add_arguments(self, parser):
        parser.add_argument("--symbol", default="XAUUSD_o")
        parser.add_argument(
            "--apply", action="store_true",
            help="Persist labels after all trust and uniqueness checks pass.",
        )

    def handle(self, *args, **options):
        symbol = options["symbol"]
        apply_changes = options["apply"]
        rows = LearningTradeOutcome.objects.filter(
            symbol=symbol,
            closed_at__isnull=False,
            r_multiple__isnull=True,
        ).order_by("closed_at", "id")

        counts = {
            "examined": 0,
            "eligible": 0,
            "updated": 0,
            "skipped": 0,
        }
        for row in rows.iterator():
            counts["examined"] += 1
            risk_amount, reason = _trusted_risk_for_outcome(row)
            if risk_amount is None:
                counts["skipped"] += 1
                self.stdout.write(
                    f"SKIP trade_id={row.trade_id}: {reason}"
                )
                continue

            realized_pnl = _decimal(row.realized_pnl)
            if realized_pnl is None:
                counts["skipped"] += 1
                self.stdout.write(
                    f"SKIP trade_id={row.trade_id}: realized PnL is invalid"
                )
                continue

            r_multiple = (realized_pnl / risk_amount).quantize(R_QUANTUM)
            counts["eligible"] += 1
            self.stdout.write(
                f"{'APPLY' if apply_changes else 'DRY-RUN'} "
                f"trade_id={row.trade_id} risk={risk_amount} R={r_multiple}"
            )
            if not apply_changes:
                continue

            with transaction.atomic():
                locked = LearningTradeOutcome.objects.select_for_update().get(pk=row.pk)
                # Never overwrite an existing label or a concurrently updated row.
                if locked.r_multiple is not None:
                    counts["skipped"] += 1
                    continue
                locked.risk_amount = risk_amount
                locked.r_multiple = r_multiple
                locked.save(update_fields=["risk_amount", "r_multiple"])
                counts["updated"] += 1

        self.stdout.write(
            "Summary: "
            + ", ".join(f"{key}={value}" for key, value in counts.items())
            + (". Changes persisted." if apply_changes else ". Dry-run only; database unchanged.")
        )
