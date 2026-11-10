"""Export independent strategy evidence without mixing future labels into features."""
import hashlib
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from ea_bridge.models import DecisionEvaluation, EACommand, LearningTradeOutcome
from ea_bridge.strategy_registry import STRATEGIES
from ea_bridge.strategy_evidence import quality_issues


class Command(BaseCommand):
    help = 'Export scoped independent-strategy JSONL for analysis; does not train or trade.'

    def add_arguments(self, parser):
        for name in ('source', 'account-login', 'server', 'symbol', 'output'):
            parser.add_argument('--' + name, required=True)
        parser.add_argument('--setup', choices=list(STRATEGIES), required=True)
        parser.add_argument('--as-of', help='Timezone-aware ISO timestamp; defaults to now.')
        parser.add_argument('--limit', type=int, default=10000)

    def handle(self, *args, **options):
        try:
            cutoff = parse_datetime(options['as_of']) if options.get('as_of') else timezone.now()
        except ValueError as exc:
            raise CommandError('Invalid --as-of timestamp') from exc
        if not cutoff or not timezone.is_aware(cutoff):
            raise CommandError('--as-of needs a timezone')
        if not 1 <= options['limit'] <= 100000:
            raise CommandError('--limit must be 1–100000')
        scope = dict(instance_id=options['source'], account_login=options['account_login'],
                     server_name=options['server'], symbol=options['symbol'])
        rows = DecisionEvaluation.objects.filter(**scope, features__strategy_setup=options['setup'],
            created_at__lte=cutoff).select_related('verdict').order_by('created_at', 'pk')
        path = Path(options['output'])
        if path.exists():
            raise CommandError('Output already exists; choose a new snapshot path')
        count = eligible_count = 0
        digest = hashlib.sha256()
        # Exclusive create; restrict locally exported account information to its owner.
        try:
            with path.open('x', encoding='utf-8') as stream:
                path.chmod(0o600)
                def emit(value):
                    line = json.dumps(value, sort_keys=True, default=str, allow_nan=False) + '\n'
                    stream.write(line)
                    digest.update(line.encode())
                emit(dict(type='manifest', schema_version=1, scope=scope, setup=options['setup'],
                    as_of=cutoff, limit=options['limit'],
                    labels='Only exactly linked closed broker outcomes; WAIT and unfilled signals are not losses.',
                    split='Split chronologically and purge overlapping holding windows. Commands and labels are never input features.'))
                for row in rows[:options['limit']].iterator(chunk_size=200):
                    issues = quality_issues(row.features.get('input_evidence'), row.created_at)
                    commands = list(EACommand.objects.filter(evaluation=row, instance_id=scope['instance_id'],
                        account_login=scope['account_login'], symbol=scope['symbol'], action='OPEN',
                        payload__server_name=scope['server_name'], created_at__lte=cutoff))
                    labels = []
                    for command in commands:
                        outcomes = LearningTradeOutcome.objects.filter(instance_id=scope['instance_id'],
                            account_login=scope['account_login'], symbol=scope['symbol'], decision_id=row.decision_id,
                            tags__command_id=command.command_id, received_at__lte=cutoff, closed_at__lte=cutoff,
                            trade_id__startswith=f"{scope['account_login']}:{scope['server_name']}:")
                        for outcome in outcomes:
                            labels.append(dict(trade_id=outcome.trade_id, command_id=command.command_id,
                                kind='BROKER_ACCOUNT_CURRENCY_NET_PNL', available_at=outcome.received_at,
                                opened_at=outcome.opened_at, closed_at=outcome.closed_at,
                                realized_pnl=outcome.realized_pnl, r_multiple=outcome.r_multiple,
                                execution_metrics=outcome.execution_metrics))
                    eligible = row.action in ('BUY', 'SELL') and bool(labels) and not issues
                    count += 1
                    eligible_count += int(eligible)
                    emit(dict(type='evaluation', evaluation_id=row.pk, decision_id=row.decision_id,
                        recorded_at=row.created_at, action=row.action, reason=row.rationale_code,
                        model_version=row.model_version, features=row.features,
                        risk_verdict=dict(approved=row.verdict.approved, reason=row.verdict.reason_code,
                                         approved_volume=row.verdict.approved_volume),
                        command_ids=[c.command_id for c in commands], labels=labels,
                        broker_training_eligible=eligible, quality_issues=issues))
                emit(dict(type='summary', evaluations=count, broker_training_eligible=eligible_count,
                          truncated=rows.count() > count))
        except OSError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(dict(output=str(path), sha256=digest.hexdigest(),
            evaluations=count, broker_training_eligible=eligible_count)))
