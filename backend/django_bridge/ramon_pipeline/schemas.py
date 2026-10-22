"""Shared event envelope and validation."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
import json
from typing import Any, Mapping
from uuid import uuid4

class PipelineError(ValueError):
    """A message violates the pipeline contract."""

class EventKind(StrEnum):
    MARKET_TICK = "market_tick"
    MARKET_BAR = "market_bar"
    EA_HEARTBEAT = "ea_heartbeat"
    EXECUTION_EVENT = "execution_event"
    STRATEGY_DECISION = "strategy_decision"
    TELEMETRY = "telemetry"

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

@dataclass(frozen=True, slots=True)
class Envelope:
    kind: EventKind
    source: str
    payload: Mapping[str, Any]
    event_id: str = field(default_factory=lambda: str(uuid4()))
    schema_version: int = 1
    occurred_at: datetime = field(default_factory=utc_now)
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        if not self.event_id.strip() or len(self.event_id) > 128:
            raise PipelineError("event_id must contain 1..128 characters")
        if not self.source.strip() or len(self.source) > 128:
            raise PipelineError("source must contain 1..128 characters")
        if self.schema_version < 1:
            raise PipelineError("schema_version must be positive")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise PipelineError("occurred_at must be timezone-aware")
        try:
            json.dumps(dict(self.payload), allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise PipelineError("payload must be finite JSON data") from exc

    def to_record(self) -> dict[str, str]:
        return {"event_id": self.event_id, "kind": self.kind.value, "source": self.source,
                "schema_version": str(self.schema_version),
                "occurred_at": self.occurred_at.astimezone(timezone.utc).isoformat(),
                "correlation_id": self.correlation_id or "",
                "payload": json.dumps(dict(self.payload), separators=(",", ":"), allow_nan=False)}
