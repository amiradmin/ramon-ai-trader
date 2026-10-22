"""Repository ports implemented later by Django ORM adapters."""
from typing import Any, Iterable, Mapping, Protocol
from .schemas import Envelope

class EventRepository(Protocol):
    def append_once(self, event: Envelope) -> bool:
        """True only when this event ID is inserted for the first time."""
        ...
    def append_market_batch(self, rows: Iterable[Mapping[str, Any]]) -> int:
        """Bulk insert/upsert normalized bars and return accepted count."""
        ...

def idempotency_key(event: Envelope) -> tuple[str, str]:
    return event.source, event.event_id
