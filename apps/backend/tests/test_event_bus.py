"""Event bus publisher (PR #7).

docs/04 §8 + docs/05 §6.6 + docs/06 §5 Спринт 1: best-effort
per-user fan-out over Redis Pub/Sub. Tested with ``fakeredis`` so
the suite stays hermetic.
"""

from __future__ import annotations

import asyncio
import json
import uuid

import fakeredis.aioredis
import pytest

from app.core.event_bus import publish_for_user, user_channel
from app.events.schemas import UserRegisteredEvent, parse_event


def _new_uuid_str() -> str:
    return str(uuid.uuid4())


def _make_event(user_id: str) -> UserRegisteredEvent:
    return UserRegisteredEvent(
        user_id=user_id,
        workspace_id=_new_uuid_str(),
        email="alice@example.com",
        locale="ru-RU",
        default_workspace_id=_new_uuid_str(),
    )


def test_user_channel_format() -> None:
    uid = uuid.uuid4()
    assert user_channel(uid) == f"events:user:{uid}"
    assert user_channel(str(uid)) == f"events:user:{uid}"


@pytest.mark.asyncio
async def test_publish_round_trip_via_fakeredis() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    user_id = _new_uuid_str()
    channel = user_channel(user_id)

    pubsub = redis.pubsub()
    await pubsub.subscribe(channel)
    # Skip the synthetic ``{"type": "subscribe"}`` ack.
    await asyncio.wait_for(
        pubsub.get_message(ignore_subscribe_messages=False, timeout=1.0),
        timeout=1.0,
    )

    event = _make_event(user_id)
    await publish_for_user(redis, user_id, event)

    msg = await asyncio.wait_for(
        pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0),
        timeout=1.0,
    )
    assert msg is not None
    data = msg["data"]
    assert isinstance(data, str)

    parsed = parse_event(json.loads(data))
    assert isinstance(parsed, UserRegisteredEvent)
    assert parsed.email == "alice@example.com"
    assert parsed.user_id == user_id
    assert parsed.event_id == event.event_id

    await pubsub.unsubscribe(channel)
    await pubsub.close()


@pytest.mark.asyncio
async def test_publish_swallows_redis_errors() -> None:
    """A pubsub blip must NOT propagate into the calling handler.

    Simulates a flaky Redis by giving the publisher a stub whose
    ``publish`` raises — the call should still complete cleanly.
    """

    class _ExplodingRedis:
        async def publish(self, *_a: object, **_kw: object) -> int:
            raise RuntimeError("connection reset")

    await publish_for_user(_ExplodingRedis(), _new_uuid_str(), _make_event(_new_uuid_str()))


@pytest.mark.asyncio
async def test_publish_wire_shape_matches_schema() -> None:
    """Serialized event must roundtrip back through the discriminator.

    Guards against accidentally publishing an event whose JSON the
    consumer can't parse (e.g. via a non-model dict in the future).
    """

    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    user_id = _new_uuid_str()
    pubsub = redis.pubsub()
    await pubsub.subscribe(user_channel(user_id))
    await asyncio.wait_for(
        pubsub.get_message(ignore_subscribe_messages=False, timeout=1.0),
        timeout=1.0,
    )

    await publish_for_user(redis, user_id, _make_event(user_id))

    msg = await asyncio.wait_for(
        pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0),
        timeout=1.0,
    )
    parsed = parse_event(json.loads(msg["data"]))
    assert parsed.event_type == "user.registered"
    assert parsed.agent_source == "platform.auth"

    await pubsub.unsubscribe(user_channel(user_id))
    await pubsub.close()
