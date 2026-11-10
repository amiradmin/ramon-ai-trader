"""Reproducible, server-owned inputs; never accepts audit metadata from API clients."""
from functools import lru_cache
import hashlib
import json
from pathlib import Path


def checksum(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, allow_nan=False).encode()).hexdigest()


@lru_cache(maxsize=1)
def code_checksums():
    root = Path(__file__).parent
    paths = [root / name for name in ('decision.py', 'strategy_worker.py', 'strategy_registry.py',
                                    'strategy_evidence.py', 'thresholds.py', 'command_producer.py')]
    paths += sorted((root.parent / 'ramon_pipeline' / 'signalflow').rglob('*.py'))
    paths += sorted((root.parent / 'ramon_pipeline' / 'signalflow').glob('*.json'))
    return {str(p.relative_to(root.parent)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def capture_inputs(rows, feed, payload, now):
    inputs = {'bars': payload['bars'], 'received_at': [r.received_at.isoformat() for r in rows],
              'quote': payload_subset(payload), 'quote_time': feed.payload.get('quote_time'),
              'telemetry_received_at': feed.last_seen.isoformat()}
    return {'schema_version': 1, 'captured_at': now.isoformat(), 'inputs': inputs,
            'input_sha256': checksum(inputs), 'source_sha256': code_checksums()}


def payload_subset(payload):
    return {key: payload.get(key) for key in ('instance_id', 'account_login', 'server_name',
                                             'symbol', 'timeframe', 'bid', 'ask', 'point')}


def quality_issues(evidence, evaluated_at):
    from django.utils.dateparse import parse_datetime
    from django.utils import timezone
    if not isinstance(evidence, dict) or evidence.get('schema_version') != 1:
        return ['LEGACY_INPUT_EVIDENCE_MISSING']
    inputs = evidence.get('inputs', {})
    issues = []
    if checksum(inputs) != evidence.get('input_sha256'):
        issues.append('INPUT_CHECKSUM_MISMATCH')
    bars, receipts = inputs.get('bars', []), inputs.get('received_at', [])
    if not bars or len(bars) != len(receipts):
        issues.append('INPUT_RECEIPTS_MISSING')
    for raw in receipts + [inputs.get('telemetry_received_at'), evidence.get('captured_at')]:
        try:
            stamp = parse_datetime(str(raw))
            if not stamp or not timezone.is_aware(stamp) or stamp > evaluated_at:
                issues.append('INPUT_RECEIPT_INVALID_OR_LATE')
        except (ValueError, TypeError):
            issues.append('INPUT_RECEIPT_INVALID_OR_LATE')
    for bar in bars:
        try:
            if bar['time'] + 900 > evaluated_at.timestamp() or bar.get('closed') is not True:
                issues.append('FORMING_OR_FUTURE_BAR')
        except (KeyError, TypeError):
            issues.append('INVALID_BAR_TIMESTAMP')
    if not evidence.get('source_sha256'):
        issues.append('CODE_PROVENANCE_MISSING')
    return sorted(set(issues))


def policy_snapshot(pipeline):
    from dataclasses import asdict
    return dict(opportunities=asdict(pipeline.opportunities.config),
                minimum_score=pipeline.opportunities.minimum_score,
                trading_settings=pipeline.opportunities.trading_settings,
                entry_timing=asdict(pipeline.entry_timer.config),
                minimum_quality=pipeline.entry_timer.minimum_quality,
                maximum_spread_points=pipeline.entry_timer.maximum_spread_points,
                risk_management=asdict(pipeline.risk_manager.config),
                maximum_risk_percent=pipeline.risk_manager.maximum_risk_percent)
