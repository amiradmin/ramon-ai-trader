"""Bounded retry policy and UTC parsing helpers."""
from dataclasses import dataclass
from datetime import datetime, timezone

@dataclass(frozen=True, slots=True)
class RecoveryPolicy:
    max_delivery_attempts: int = 5
    pending_idle_ms: int = 30_000
    execution_event_retention_days: int = 90
    def __post_init__(self) -> None:
        if min(self.max_delivery_attempts, self.pending_idle_ms, self.execution_event_retention_days) < 1:
            raise ValueError("recovery limits must be positive")

def parse_utc(value: str) -> datetime:
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return stamp.astimezone(timezone.utc)

def should_quarantine(delivery_count: int, policy: RecoveryPolicy) -> bool:
    return delivery_count >= policy.max_delivery_attempts
