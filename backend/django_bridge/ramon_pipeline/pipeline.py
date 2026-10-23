"""Minimal ingestion pipeline: validate the envelope and enqueue it in Redis."""
from .schemas import Envelope, PipelineError
from .redis_buffer import RedisStreamBuffer


class RamonPipeline:
    def __init__(self, buffer: RedisStreamBuffer):
        self.buffer = buffer

    def accept(self, envelope: Envelope) -> str:
        if not isinstance(envelope, Envelope):
            raise PipelineError("Envelope instance required")
        return self.buffer.publish(envelope)
