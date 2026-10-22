"""Redis Streams adapter; Redis buffers events but is not the source of truth."""
from typing import Protocol, Mapping, Any
from .schemas import Envelope

class StreamClient(Protocol):
    def xadd(self, name: str, fields: Mapping[str, str], *, maxlen: int | None = None, approximate: bool = True) -> Any: ...

STREAMS = {"market": "ramon:market:v1", "execution": "ramon:execution:v1", "telemetry": "ramon:telemetry:v1"}

class RedisStreamBuffer:
    def __init__(self, client: StreamClient, *, maxlen: int = 100_000) -> None:
        if maxlen < 1:
            raise ValueError("maxlen must be positive")
        self.client, self.maxlen = client, maxlen

    def publish(self, envelope: Envelope) -> str:
        kind = envelope.kind.value
        lane = "execution" if kind == "execution_event" else ("market" if kind in {"market_tick", "market_bar"} else "telemetry")
        # Never auto-trim execution events: consumers may still need to process them.
        # Configure and monitor execution retention separately at the Redis deployment layer.
        trim_limit = None if lane == "execution" else self.maxlen
        return str(self.client.xadd(STREAMS[lane], envelope.to_record(), maxlen=trim_limit, approximate=True))
