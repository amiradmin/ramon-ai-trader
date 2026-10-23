"""Persist Redis Stream events to Django/PostgreSQL before acknowledging them."""
import json
import logging
import socket
import time
from datetime import datetime, timezone

from django.core.management.base import BaseCommand
from django.db import close_old_connections, transaction
from django.utils.dateparse import parse_datetime
from redis import Redis
from redis.exceptions import ResponseError

from django.conf import settings
from ea_bridge.models import EAEvent, MarketBar
from ramon_pipeline.redis_buffer import STREAMS

log = logging.getLogger(__name__)


def _decode_event(fields):
    event_id = fields.get("event_id", "")
    instance_id = fields.get("source", "")
    event_type = fields.get("event_type", "")
    raw_payload = fields.get("payload", "{}")
    payload = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
    if not isinstance(payload, dict):
        raise ValueError("event payload must be a JSON object")
    if not event_type:
        event_type = str(payload.get("event_type") or fields.get("kind") or "TELEMETRY").upper()
    occurred_raw = fields.get("occurred_at", "")
    occurred_at = parse_datetime(occurred_raw) if occurred_raw else None
    if occurred_at is not None and occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=timezone.utc)
    if not event_id or not instance_id:
        raise ValueError("event_id and source are required")
    return event_id, instance_id, event_type[:64], occurred_at, payload


class Command(BaseCommand):
    help = "Consume Ramon EA Redis Streams and persist snapshots/events to PostgreSQL."

    @staticmethod
    def _persist(fields):
        """Idempotently persist one decoded Redis record; caller ACKs after this returns."""
        event_id, instance_id, event_type, occurred_at, payload = _decode_event(fields)
        kind = str(fields.get("kind", "")).lower()
        if kind == "market_bar" and all(key in payload for key in ("symbol", "timeframe", "timestamp", "open", "high", "low", "close")):
            bar_time = parse_datetime(str(payload["timestamp"]))
            if bar_time is None:
                raise ValueError("market bar timestamp must be an ISO datetime")
            if bar_time.tzinfo is None:
                bar_time = bar_time.replace(tzinfo=timezone.utc)
            MarketBar.objects.get_or_create(
                source=instance_id,
                symbol=str(payload["symbol"])[:32],
                timeframe=str(payload["timeframe"]).upper()[:16],
                timestamp=bar_time,
                defaults={
                    "open": payload["open"],
                    "high": payload["high"],
                    "low": payload["low"],
                    "close": payload["close"],
                    "volume": payload.get("volume", 0),
                },
            )
            return
        with transaction.atomic():
            EAEvent.objects.get_or_create(
                event_id=event_id,
                defaults={
                    "instance_id": instance_id,
                    "event_type": event_type,
                    "occurred_at": occurred_at,
                    "payload": payload,
                },
            )

    def add_arguments(self, parser):
        parser.add_argument("--count", type=int, default=100)
        parser.add_argument("--block-ms", type=int, default=1000)
        parser.add_argument("--group", default="ramon-django-persist")
        parser.add_argument("--consumer", default=settings.RAMON_REDIS_CONSUMER)

    def handle(self, *args, **options):
        if options["count"] < 1 or options["block_ms"] < 0:
            raise ValueError("--count must be positive and --block-ms cannot be negative")
        client = Redis.from_url(settings.RAMON_REDIS_URL, decode_responses=True,
                                socket_connect_timeout=3, socket_timeout=5)
        group = options["group"]
        consumer = options["consumer"] or socket.gethostname()
        streams = list(STREAMS.values())
        for stream in streams:
            try:
                client.xgroup_create(stream, group, id="0-0", mkstream=True)
            except ResponseError as exc:
                if "BUSYGROUP" not in str(exc):
                    raise
        self.stdout.write(self.style.SUCCESS(
            f"Consuming {len(streams)} streams as {consumer}; group={group}"
        ))
        while True:
            close_old_connections()
            try:
                # Reclaim messages left pending by a crashed consumer before reading new data.
                for stream in streams:
                    try:
                        _next, reclaimed, _cursor = client.xautoclaim(
                            stream, group, consumer, min_idle_time=60_000,
                            start_id="0-0", count=options["count"],
                        )
                    except (AttributeError, ResponseError):
                        reclaimed = []
                    for message_id, fields in reclaimed:
                        try:
                            self._persist(fields)
                            client.xack(stream, group, message_id)
                        except Exception:
                            log.exception("Failed to recover pending event stream=%s id=%s", stream, message_id)

                batches = client.xreadgroup(
                    group, consumer, {stream: ">" for stream in streams},
                    count=options["count"], block=options["block_ms"] or None,
                )
                for stream, messages in batches:
                    for message_id, fields in messages:
                        try:
                            self._persist(fields)
                            # Acknowledge only after the database transaction has committed.
                            client.xack(stream, group, message_id)
                        except Exception:
                            log.exception("Failed to persist Redis event stream=%s id=%s", stream, message_id)
                            # Leave it pending so a later recovery pass can retry it.
            except KeyboardInterrupt:
                self.stdout.write("Stopping Redis stream consumer.")
                break
            except Exception:
                log.exception("Redis stream consumer loop failed; retrying")
                time.sleep(2)
            finally:
                close_old_connections()
