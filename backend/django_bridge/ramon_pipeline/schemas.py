"""Transport schemas for EA events queued through Redis Streams."""
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import json


class PipelineError(RuntimeError):
    """Raised when an event cannot be validated or queued."""


class EventKind(str, Enum):
    MARKET_BAR = "market_bar"
    MARKET_TICK = "market_tick"
    EXECUTION_EVENT = "execution_event"
    TELEMETRY = "telemetry"


@dataclass(frozen=True)
class Envelope:
    kind: EventKind
    source: str
    payload: dict
    event_id: str
    occurred_at: datetime

    def to_record(self) -> dict[str, str]:
        if not self.event_id or not self.source:
            raise PipelineError("event_id and source are required")
        occurred = self.occurred_at
        if occurred.tzinfo is None or occurred.utcoffset() is None:
            raise PipelineError("occurred_at must be timezone-aware")
        return {
            "kind": self.kind.value,
            "source": self.source,
            "event_id": self.event_id,
            "occurred_at": occurred.astimezone(timezone.utc).isoformat(),
            "payload": json.dumps(self.payload, separators=(",", ":"), ensure_ascii=False),
        }
