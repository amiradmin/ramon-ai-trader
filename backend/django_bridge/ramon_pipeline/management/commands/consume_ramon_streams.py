"""Consume Redis Streams and commit each message before acknowledging it."""
import json
import logging
from datetime import datetime
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from redis import Redis
from redis.exceptions import ResponseError

from ea_bridge.models import EAEvent, MarketBar, OrderIntentRecord, RiskVerdictRecord, StrategyDecisionRecord
from ramon_pipeline.market_data import normalize_bar
from ramon_pipeline.redis_buffer import STREAMS
from ramon_pipeline.recovery import RecoveryPolicy

log = logging.getLogger(__name__)
GROUP = "ramon-django-v1"
DEAD_LETTER_STREAM = "ramon:deadletter:v1"


class Command(BaseCommand):
    help = "Consume Ramon Redis Streams and persist events idempotently."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Process one read batch then exit")
        parser.add_argument("--count", type=int, default=100)
        parser.add_argument("--block-ms", type=int, default=1000)

    def handle(self, *args, **opts):
        if opts["count"] < 1 or opts["block_ms"] < 1:
            raise ValueError("count and block-ms must be positive")
        client = Redis.from_url(settings.RAMON_REDIS_URL, decode_responses=True,
                                socket_connect_timeout=3, health_check_interval=30)
        client.ping()
        for stream in STREAMS.values():
            try:
                client.xgroup_create(stream, GROUP, id="0-0", mkstream=True)
            except ResponseError as exc:
                if "BUSYGROUP" not in str(exc):
                    raise
        policy = RecoveryPolicy()
        consumer = settings.RAMON_REDIS_CONSUMER
        while True:
            processed = 0
            # Reclaim stale pending entries before reading new messages.
            for stream in STREAMS.values():
                claimed = client.xautoclaim(stream, GROUP, consumer,
                                            min_idle_time=policy.pending_idle_ms,
                                            start_id="0-0", count=opts["count"])
                for message_id, record in claimed[1]:
                    if self._process(client, stream, message_id, record, policy):
                        processed += 1
            result = client.xreadgroup(GROUP, consumer, {stream: ">" for stream in STREAMS.values()},
                                       count=opts["count"], block=opts["block_ms"])
            for stream, messages in result:
                for message_id, record in messages:
                    if self._process(client, stream, message_id, record, policy):
                        processed += 1
            if opts["once"]:
                self.stdout.write(f"processed={processed}")
                return

    def _process(self, client, stream, message_id, record, policy):
        try:
            self._persist(record)
        except Exception:
            log.exception("Ramon stream persistence failed stream=%s id=%s", stream, message_id)
            pending = client.xpending_range(stream, GROUP, min=message_id, max=message_id, count=1)
            attempts = int(pending[0].get("times_delivered", 1)) if pending else 1
            if attempts >= policy.max_delivery_attempts:
                client.xadd(DEAD_LETTER_STREAM, {"original_stream": stream, "original_id": message_id,
                                                "failure_reason": "persistence_error",
                                                "record": json.dumps(record, separators=(",", ":"))})
                client.xack(stream, GROUP, message_id)
                log.error("Ramon message quarantined stream=%s id=%s attempts=%s", stream, message_id, attempts)
                return True
            return False
        client.xack(stream, GROUP, message_id)
        return True

    @transaction.atomic
    def _persist(self, record):
        kind, source, event_id = record["kind"], record["source"], record["event_id"]
        payload = json.loads(record["payload"])
        occurred_raw = record.get("occurred_at") or ""
        occurred = datetime.fromisoformat(occurred_raw.replace("Z", "+00:00")) if occurred_raw else None

        if kind == "market_bar":
            bar = normalize_bar(payload)
            MarketBar.objects.update_or_create(
                source=source, symbol=bar["symbol"], timeframe=bar["timeframe"], timestamp=bar["timestamp"],
                defaults={key: bar[key] for key in ("open", "high", "low", "close", "volume")})
            return
        if kind == "strategy_decision":
            StrategyDecisionRecord.objects.get_or_create(decision_id=str(payload["decision_id"]), defaults={
                "symbol": str(payload["symbol"]), "action": str(payload["action"]).upper(),
                "confidence": float(payload["confidence"]), "rationale_code": str(payload.get("rationale_code", "")),
                "model_version": str(payload["model_version"]), "features": payload.get("features", {})})
        elif kind == "risk_verdict":
            RiskVerdictRecord.objects.get_or_create(decision_id=str(payload["decision_id"]), reason_code=str(payload["reason_code"]), defaults={
                "approved": bool(payload.get("approved", False)), "approved_volume": Decimal(str(payload.get("approved_volume", 0))),
                "stop_loss": payload.get("stop_loss"), "take_profit": payload.get("take_profit"), "details": payload.get("details", {})})
        elif kind == "order_intent":
            OrderIntentRecord.objects.get_or_create(intent_id=str(payload["intent_id"]), defaults={
                "decision_id": str(payload["decision_id"]), "instance_id": str(payload["instance_id"]),
                "symbol": str(payload["symbol"]), "side": str(payload["side"]).upper(),
                "volume": Decimal(str(payload["volume"])), "stop_loss": Decimal(str(payload["stop_loss"])),
                "take_profit": payload.get("take_profit"), "expires_at": payload.get("expires_at")})
        # The event ledger is the durable record for fills, rejects and telemetry.
        EAEvent.objects.get_or_create(event_id=event_id, defaults={
            "instance_id": source, "event_type": kind.upper(), "occurred_at": occurred, "payload": payload})
