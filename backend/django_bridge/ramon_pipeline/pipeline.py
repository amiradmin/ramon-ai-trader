"""Top-level composition boundary; concrete adapters are injected."""
from .redis_buffer import RedisStreamBuffer
from .schemas import Envelope

class RamonPipeline:
    def __init__(self, buffer: RedisStreamBuffer) -> None:
        self.buffer = buffer
    def accept(self, envelope: Envelope) -> str:
        """Publish to a lane-specific stream; workers persist asynchronously."""
        return self.buffer.publish(envelope)
