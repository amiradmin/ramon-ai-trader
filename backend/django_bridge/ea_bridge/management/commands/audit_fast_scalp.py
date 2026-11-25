"""Summarize recorded Fast Scalp signal, data and execution gates without running workers."""
import json
from collections import Counter
from datetime import timedelta
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.conf import settings
from ea_bridge.models import EAHeartbeat, ScalperControl, ScalpObservation, ScalpAuditEvent, MarketBar
from ea_bridge.scalper_engine import readiness


class Command(BaseCommand):
    help = "Read-only Fast Scalp gate evidence for one explicit account scope."

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True)
        parser.add_argument("--symbol", required=True)
        parser.add_argument("--hours", type=int, default=24)

    def handle(self, *args, **options):
        if not 1 <= options["hours"] <= 168:
            raise CommandError("Hours must be between 1 and 168")
        feeds = list(EAHeartbeat.objects.filter(instance_id=options["source"],
                     symbol=options["symbol"])[:2])
        if len(feeds) != 1:
            raise CommandError("Expected exactly one matching feed; cannot infer account scope")
        feed = feeds[0]
        now = timezone.now()
        since = now - timedelta(hours=options["hours"])
        scope = dict(source=feed.instance_id, symbol=feed.symbol,
                     account_login=feed.account_login, server_name=feed.server_name)
        control = ScalperControl.objects.filter(**scope).first()
        observations = ScalpObservation.objects.filter(**scope, recorded_at__gte=since, recorded_at__lte=now)
        events = ScalpAuditEvent.objects.filter(**scope, observed_at__gte=since, observed_at__lte=now)
        raw = Counter()
        effective = Counter()
        for row in observations.values("action", "reason", "snapshot", "status").iterator():
            plan = (row["snapshot"] or {}).get("raw_signal") or {}
            raw[str(plan.get("reason") or "RAW_SIGNAL_UNAVAILABLE")] += 1
            effective[str(row["reason"])] += 1
        by_kind = {}
        for kind, reason in events.values_list("kind", "reason").iterator():
            by_kind.setdefault(kind, Counter())[reason] += 1
        bars = {}
        for tf, seconds in (("M1", 60), ("M5", 300)):
            stamps = list(MarketBar.objects.filter(source=feed.instance_id, symbol=feed.symbol,
                timeframe=tf, timestamp__gte=since, timestamp__lte=now-timedelta(seconds=seconds),
                received_at__lte=now).order_by("timestamp").values_list("timestamp", flat=True))
            bars[tf] = dict(completed=len(stamps), latest=stamps[-1] if stamps else None,
                age_since_close_seconds=(now-stamps[-1]).total_seconds()-seconds if stamps else None,
                gaps=sum((b-a).total_seconds()>seconds for a,b in zip(stamps, stamps[1:])))
        result = dict(mode="READ_ONLY_RECORDED_EVIDENCE", source=feed.instance_id, symbol=feed.symbol,
            since=since, until=now, readiness=readiness(feed, now=now),
            control_present=control is not None, automatic=bool(control and control.automatic),
            flags={k: getattr(settings,k,False) for k in ("RAMON_SCALP_SHADOW_ENABLED",
                "RAMON_SCALP_EXECUTION_ENABLED","RAMON_EA_COMMAND_CREATION_ENABLED",
                "RAMON_EA_COMMAND_EXECUTION_ENABLED")},
            bars=bars, observations=sum(effective.values()), raw_signal_reasons=dict(raw),
            effective_signal_reasons=dict(effective), audit_events={k:dict(v) for k,v in by_kind.items()},
            limitations="Recorded gate events are deduplicated, not counts of every worker cycle. Missing events are not proof of readiness. No worker invoked and no order created.")
        self.stdout.write(json.dumps(result, indent=2, default=str))
