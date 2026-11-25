"""Summarize recorded Fast Scalp signal, data and execution gates without running workers."""
import json
from collections import Counter
from datetime import timedelta
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.conf import settings
from ea_bridge.models import EAHeartbeat, ScalperControl, ScalpObservation, ScalpAuditEvent, MarketBar, EACommand
from ea_bridge.scalper_engine import readiness


def distribution(values):
    values = sorted(values)
    if not values:
        return dict(count=0)
    return dict(count=len(values), minimum=values[0], median=values[(len(values)-1)//2],
                p95=values[max(0, (95*len(values)+99)//100-1)], maximum=values[-1])


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
        lifecycle = Counter()
        command_states = Counter()
        examples = []
        for obs in observations.filter(action__in=["BUY","SELL"]).order_by("-recorded_at").iterator():
            linked = list(EACommand.objects.filter(instance_id=feed.instance_id,
                account_login=feed.account_login, symbol=feed.symbol,
                action=EACommand.Action.OPEN, payload__scalp_observation_id=obs.pk,
                created_at__lte=now).values("state","created_at"))
            lifecycle["directional_observations"] += 1
            lifecycle["with_open_command" if linked else "without_open_command"] += 1
            command_states.update(x["state"] for x in linked)
            if len(examples) < 30:
                examples.append(dict(observation_id=obs.pk, signal_at=obs.signal_bar_time,
                    action=obs.action, paper_status=obs.status,
                    linked_commands=linked,
                    recorded_gate_reasons=list(events.filter(observation=obs).values_list("reason",flat=True))))
        bars = {}
        for tf, seconds in (("M1", 60), ("M5", 300)):
            records = list(MarketBar.objects.filter(source=feed.instance_id, symbol=feed.symbol,
                timeframe=tf, timestamp__gte=since, timestamp__lte=now-timedelta(seconds=seconds),
                received_at__lte=now).order_by("timestamp").values_list("timestamp", "received_at"))
            stamps = [t for t, _ in records]
            lag = [(received-t).total_seconds()-seconds for t,received in records]
            bars[tf] = dict(completed=len(stamps), latest=stamps[-1] if stamps else None,
                age_since_close_seconds=(now-stamps[-1]).total_seconds()-seconds if stamps else None,
                gaps=sum((b-a).total_seconds()>seconds for a,b in zip(stamps, stamps[1:])),
                first_record_receipt_minus_close_seconds=distribution(lag),
                received_before_close=sum(v<0 for v in lag),
                largest_interbar_gap_seconds=max(((b-a).total_seconds() for a,b in zip(stamps, stamps[1:])),default=None))
        result = dict(mode="READ_ONLY_RECORDED_EVIDENCE", source=feed.instance_id, symbol=feed.symbol,
            since=since, until=now, readiness=readiness(feed, now=now),
            control_present=control is not None, automatic=bool(control and control.automatic),
            flags={k: getattr(settings,k,False) for k in ("RAMON_SCALP_SHADOW_ENABLED",
                "RAMON_SCALP_EXECUTION_ENABLED","RAMON_EA_COMMAND_CREATION_ENABLED",
                "RAMON_EA_COMMAND_EXECUTION_ENABLED")},
            diagnostic_version=3, lifecycle=dict(lifecycle), linked_open_command_states=dict(command_states),
            lifecycle_examples=examples, paper_status_counts=dict(Counter(observations.values_list("status",flat=True))),
            bars=bars, observations=sum(effective.values()), raw_signal_reasons=dict(raw),
            effective_signal_reasons=dict(effective), audit_events={k:dict(v) for k,v in by_kind.items()},
            limitations="Recorded gate events are deduplicated, not counts of every worker cycle. Missing events are not proof of readiness. Receipt timestamps are first insertion, not final candle delivery; negative lags can represent early insertion. Command states are current snapshots, not confirmed broker fills. Links use exact observation IDs; unlinked observations are not proven missed orders. No worker invoked and no order created.")
        self.stdout.write(json.dumps(result, indent=2, default=str))
