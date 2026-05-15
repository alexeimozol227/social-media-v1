"""Redis client factory.

Lazy singleton on top of ``redis.asyncio``. Tests substitute the
``get_redis`` dependency with ``fakeredis.aioredis.FakeRedis`` via the
override mechanism in ``tests/conftest.py``.
"""

from __future__ import annotations

import contextlib
from typing import Any

from redis.asyncio import Redis, from_url

from app.core.config import settings

_redis: Any | None = None


def get_redis() -> Any:
    """Return the process-wide async Redis client.

    Type is ``Any`` because the test suite swaps in a ``FakeRedis``
    instance whose return-type contracts only structurally match
    redis-py (bytes-or-str union types throughout).
    """

    global _redis
    if _redis is None:
        _redis = from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
    return _redis


async def reset_redis_singleton() -> None:
    """Drop the cached client. Used by tests when swapping fakes."""

    global _redis
    if _redis is not None:
        with contextlib.suppress(Exception):
            await _redis.aclose()
    _redis = None


def set_redis_for_tests(client: Redis) -> None:
    """Force-install a test Redis. Production code never calls this."""

    global _redis
    _redis = client
