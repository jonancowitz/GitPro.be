from collections.abc import AsyncIterator

from redis.asyncio import Redis

from app.core.config import settings


def create_redis() -> Redis:
    return Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
    )


async def get_redis() -> AsyncIterator[Redis]:
    client = create_redis()
    try:
        yield client
    finally:
        await client.aclose()
