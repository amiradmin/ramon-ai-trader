"""Django-side adapter construction, kept separate from transport contracts."""
from django.conf import settings
from redis import Redis
from .pipeline import RamonPipeline
from .redis_buffer import RedisStreamBuffer


def build_pipeline() -> RamonPipeline:
    client = Redis.from_url(settings.RAMON_REDIS_URL, decode_responses=True,
                            socket_connect_timeout=2, socket_timeout=2,
                            health_check_interval=30)
    client.ping()
    return RamonPipeline(RedisStreamBuffer(client, maxlen=settings.RAMON_REDIS_MARKET_MAXLEN))
