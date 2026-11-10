"""Read-only scoped JSONL export. Features and future labels remain distinct."""
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from ea_bridge.models import ScalpObservation, ScalpAuditEvent, LearningTradeOutcome
from ea_bridge.scalper_audit import checksum


class Command(BaseCommand):
    help = 'Export scoped scalper evidence to local JSONL; never train, enable trading, or write database rows.'
    def add_arguments(self,parser):
        for key in ['source','account-login','server','symbol','output']:
            parser.add_argument('--'+key,required=True)
        parser.add_argument('--as-of',help='Timezone-aware ISO timestamp; defaults to now.')
        parser.add_argument('--limit',type=int,default=10000)

    def handle(self,*args,**options):
        try:
            cutoff = parse_datetime(options['as_of']) if options.get('as_of') else timezone.now()
        except ValueError as exc:
            raise CommandError('Invalid --as-of timestamp') from exc
        if not cutoff or not timezone.is_aware(cutoff): raise CommandError('--as-of needs a timezone')
        if not 1 <= options['limit'] <= 100000: raise CommandError('--limit must be 1–100000')
        scope = dict(source=options['source'],account_login=options['account_login'],
                     server_name=options['server'],symbol=options['symbol'])
        path = Path(options['output'])
        if path.exists(): raise CommandError('Output already exists; choose a new evidence snapshot path')
        counts, quality = Counter(),Counter()
        digest = hashlib.sha256()
        with path.open('x',encoding='utf-8') as stream:
            def emit(value):
                line = json.dumps(value,sort_keys=True,default=str,allow_nan=False)+'\n'
                stream.write(line);digest.update(line.encode());counts[value['type']]+=1
            emit(dict(type='manifest',schema_version=2,scope=scope,as_of=cutoff,
                labels='Paper price outcomes and broker currency P&L are separate; WAIT is not a loss',
                split='Chronological by recorded_at; purge overlapping future-label windows across splits',
                limit_per_record_type=options['limit']))
            for row in ScalpObservation.objects.filter(**scope,recorded_at__lte=cutoff).order_by('recorded_at','pk')[:options['limit']]:
                snap = row.snapshot
                issues = []
                if snap.get('provenance',{}).get('schema_version') != 2: issues.append('LEGACY_PROVENANCE_MISSING')
                if not ScalpAuditEvent.objects.filter(**scope,event_key=snap.get('policy_artifact_key',''),kind='POLICY_ARTIFACT',observed_at__lte=row.recorded_at).exists():
                    issues.append('POLICY_ARTIFACT_UNAVAILABLE')
                if checksum(snap.get('inputs',{})) != snap.get('input_sha256'): issues.append('INPUT_CHECKSUM_MISMATCH')
                receipts = snap.get('input_received_at',{})
                for tf,interval in [('M1',60),('M5',300)]:
                    bars = snap.get('inputs',{}).get(tf,[])
                    stamps = receipts.get(tf,[])
                    if len(stamps)!=len(bars): issues.append(tf+'_RECEIPTS_MISSING')
                    for raw in stamps:
                        try:
                            stamp = parse_datetime(raw)
                            if not stamp or not timezone.is_aware(stamp) or stamp>row.recorded_at:
                                issues.append(tf+'_LATE_RECEIPT')
                        except (ValueError,TypeError): issues.append(tf+'_INVALID_RECEIPT')
                    if any(b.get('time',float('inf'))+interval>row.recorded_at.timestamp() for b in bars):
                        issues.append(tf+'_FORMING_OR_FUTURE_INPUT')
                paper = None
                if row.status=='SCORED' and row.settled_at and row.settled_at<=cutoff:
                    paper = dict(kind='SIMULATED_PRICE_POINTS_AND_R',available_at=row.settled_at,outcome=row.outcome)
                state = row.status if row.settled_at is None or row.settled_at<=cutoff else 'WAITING_AS_OF_CUTOFF'
                issues = sorted(set(issues));quality.update(issues)
                eligible = paper is not None and not issues
                counts['paper_training_eligible'] += int(eligible)
                emit(dict(type='evaluation',id=row.pk,version=row.version,signal_bar_time=row.signal_bar_time,
                    recorded_at=row.recorded_at,action=row.action,reason=row.reason,status_as_of=state,
                    features=snap,labels={'paper':paper},label_window_end=row.evaluation_end,
                    paper_training_eligible=eligible,quality_issues=issues))
            for row in ScalpAuditEvent.objects.filter(**scope,observed_at__lte=cutoff).order_by('observed_at','pk')[:options['limit']]:
                emit(dict(type='audit_event',event_key=row.event_key,observed_at=row.observed_at,
                    observation_id=row.observation_id,command_id=row.command_id,kind=row.kind,
                    reason=row.reason,details=row.details))
            # Receipt cutoff, not just closed_at, prevents post-cutoff broker labels.
            for row in LearningTradeOutcome.objects.filter(instance_id=options['source'],account_login=options['account_login'],
                symbol=options['symbol'],trade_id__startswith=f"{options['account_login']}:{options['server']}:",
                tags__strategy_profile='SCALPER_M1_M5',received_at__lte=cutoff,closed_at__lte=cutoff).order_by('closed_at','pk')[:options['limit']]:
                emit(dict(type='broker_outcome',trade_id=row.trade_id,opened_at=row.opened_at,closed_at=row.closed_at,
                    label_available_at=row.received_at,label_kind='BROKER_ACCOUNT_CURRENCY_NET_PNL',
                    realized_pnl=str(row.realized_pnl),r_multiple=str(row.r_multiple) if row.r_multiple is not None else None,
                    execution_metrics=row.execution_metrics,decision_snapshot=row.decision_snapshot,tags=row.tags))
            emit(dict(type='summary',counts=dict(counts),quality_issues=dict(quality)))
        self.stdout.write(json.dumps(dict(output=str(path),sha256=digest.hexdigest(),counts=dict(counts),quality_issues=dict(quality))))
