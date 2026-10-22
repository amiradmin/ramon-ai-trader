"""Fast-path request validation before queueing."""
from typing import Any, Mapping
from uuid import uuid4
from .schemas import Envelope, EventKind, PipelineError

def validate_body_size(raw_body: bytes, max_bytes: int) -> None:
    if max_bytes < 1:
        raise PipelineError("configured request limit must be positive")
    if len(raw_body) > max_bytes:
        raise PipelineError("request body too large")

def envelope_from_ea(payload: Mapping[str, Any], *, kind: EventKind, source: str = "mt5-ea") -> Envelope:
    if not isinstance(payload, Mapping):
        raise PipelineError("payload must be a JSON object")
    event_id = str(payload.get("event_id") or "").strip()
    if kind in {EventKind.EXECUTION_EVENT, EventKind.MARKET_TICK, EventKind.MARKET_BAR} and not event_id:
        raise PipelineError("event_id is required for execution and market events")
    return Envelope(kind=kind, source=source, payload=dict(payload), event_id=event_id or str(uuid4()))
