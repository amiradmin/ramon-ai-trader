"""Durable scalar/event evidence. No order creation or trading permissions."""
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import platform

from django.conf import settings
from django.utils import timezone
from .models import ScalpAuditEvent, ScalpObservation, EACommand

SCHEMA = 2


def checksum(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,default=str,allow_nan=False).encode()).hexdigest()


@lru_cache(maxsize=1)
def source_texts():
    from ramon_pipeline.signalflow import scalping
    paths = [Path(scalping.__file__), Path(__file__).with_name('scalp_observer.py'),
             Path(__file__).with_name('scalper_engine.py'), Path(__file__)]
    return {p.name:p.read_text() for p in paths}


@lru_cache(maxsize=1)
def provenance():
    from ramon_pipeline.signalflow import scalping
    import django
    return dict(schema_version=SCHEMA,python=platform.python_version(),django=django.get_version(),
                source_sha256={name:hashlib.sha256(text.encode()).hexdigest() for name,text in source_texts().items()})


def append_event(*, source, account_login, server_name, symbol, kind, reason, identity,
                 details=None, observation=None, command=None, now=None):
    key = checksum([source,account_login,server_name,symbol,kind,identity])
    row, _ = ScalpAuditEvent.objects.get_or_create(event_key=key,defaults=dict(
        source=source,account_login=account_login,server_name=server_name,symbol=symbol,
        kind=kind,reason=reason[:128],details=details or {},observation=observation,command=command,
        observed_at=now or timezone.now()))
    return row


def feed_event(feed, kind, reason, *, identity, details=None, observation=None, command=None, now=None):
    return append_event(source=feed.instance_id,account_login=feed.account_login,
        server_name=feed.server_name,symbol=feed.symbol,kind=kind,reason=reason,identity=identity,
        details=details,observation=observation,command=command,now=now)


def health_skip(feed, reason, now):
    # At most one occurrence per reason/feed/minute; no fictitious strategy label.
    return feed_event(feed,'DATA_UNAVAILABLE',reason,identity=[reason,int(now.timestamp())//60],now=now,
        details={'schema_version':SCHEMA,'heartbeat_at':feed.last_seen.isoformat(),
                 'quote_time':feed.payload.get('quote_time') if isinstance(feed.payload,dict) else None})


def gate_event(feed, control, observation, command, reason, now, context=None):
    flags = {'automatic':control.automatic,'risk_percent':str(control.risk_percent),
             'execution_gate':getattr(settings,'RAMON_SCALP_EXECUTION_ENABLED',False),
             'command_creation_gate':getattr(settings,'RAMON_EA_COMMAND_CREATION_ENABLED',False),
             'command_execution_gate':getattr(settings,'RAMON_EA_COMMAND_EXECUTION_ENABLED',False)}
    if context:
        flags['automatic'] = context['automatic']
        flags['risk_percent'] = context['risk_percent']
    observation_id = observation.pk
    if (observation.source,observation.account_login,observation.server_name,observation.symbol) != (
        feed.instance_id,feed.account_login,feed.server_name,feed.symbol):
        observation = None
    return feed_event(feed,'ENTRY_GATE',reason,identity=[observation_id,reason,flags],now=now,
        observation=observation,command=command,
        details={'schema_version':SCHEMA,'controls':flags,
                 'engine_inputs':context or {},
                 'command_payload':command.payload if command else None,
                 'meaning':'Gate result; no trade outcome label'})


def command_event(command, kind, *, now=None):
    if command.payload.get('engine') != 'SCALPER':
        return None
    original = command
    if command.action != 'OPEN':
        original = EACommand.objects.filter(command_id=command.payload.get('original_command_id'),
            instance_id=command.instance_id,account_login=command.account_login,
            payload__server_name=command.payload.get('server_name'),payload__engine='SCALPER').first()
    observation = None
    if original:
        observation = ScalpObservation.objects.filter(pk=original.payload.get('scalp_observation_id'),
            source=command.instance_id,account_login=command.account_login,
            server_name=command.payload.get('server_name'),symbol=command.symbol).first()
    return append_event(source=command.instance_id,account_login=command.account_login,
        server_name=command.payload.get('server_name',''),symbol=command.symbol,kind=kind,
        reason=command.state,identity=[command.command_id,command.state,command.result],
        observation=observation,command=command,now=now,
        details={'schema_version':SCHEMA,'action':command.action,'result':command.result,
                 'created_at':command.created_at.isoformat(),
                 'claimed_at':command.claimed_at.isoformat() if command.claimed_at else None,
                 'completed_at':command.completed_at.isoformat() if command.completed_at else None})


def policy_artifact(feed, now):
    meta = provenance()
    return feed_event(feed,'POLICY_ARTIFACT','FROZEN_SOURCE_BUNDLE',identity=meta,now=now,
        details={'provenance':meta,'source_texts':source_texts()})
