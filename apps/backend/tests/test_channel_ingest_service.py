"""Unit tests for :mod:`app.services.channel_ingest` (PR #16).

Covers the live-ingest service layer:

* happy path: unknown ``tg_message_id`` \u2192 row inserted, single
  ``channel.post_received`` event fans out to every workspace member
* duplicate ``tg_message_id`` \u2192 row reused, no event published
* unknown channel \u2192 silent drop, no DB or event side effects
* edit for an existing row \u2192 row text / media updated,
  ``channel.post_edited`` fanned out
* edit for an unknown row \u2192 falls back to insert + emits
  ``channel.post_received``
* RLS isolation: a post in channel A does not leak to a workspace
  that's only connected to channel B
* multi-workspace fan-out: one channel shared by two workspaces
  \u2192 each owner_user_id receives the event

The tests use a real :class:`AsyncSession` against the SQLite test
schema and ``fakeredis`` so the per-user pub/sub channels are
inspected by name (``events:user:{uuid}``).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import fakeredis.aioredis
import pytest
import pytest_asyncio
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.event_bus import user_channel
from app.models.brand import Brand
from app.models.channel import (
    Channel,
    ChannelPost,
    WorkspaceChannel,
    WorkspaceChannelRoleValues,
)
from app.models.user import User, UserStatus
from app.models.workspace import Workspace, WorkspaceType
from app.models.workspace_member import WorkspaceMember, WorkspaceMemberRole
from app.services.channel_ingest import LiveIngestResult, ingest_live_post

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _chat(chat_id: int) -> dict[str, Any]:
    return {
        "id": chat_id,
        "type": "channel",
        "title": "Test Channel",
        "username": "test_channel",
    }


def _build_message(
    *,
    chat_id: int = -1001234567890,
    message_id: int = 1001,
    text: str | None = "hello world",
    edit_date: int | None = None,
    posted_at_unix: int = 1_700_000_000,
) -> Message:
    payload: dict[str, Any] = {
        "message_id": message_id,
        "date": posted_at_unix,
        "chat": _chat(chat_id),
    }
    if text is not None:
        payload["text"] = text
    if edit_date is not None:
        payload["edit_date"] = edit_date
    return Message.model_validate(payload)


async def _make_user(
    db_session: AsyncSession,
    *,
    email: str,
) -> User:
    user = User(
        email=email,
        hashed_password="x",
        full_name="Tester",
        locale="ru-RU",
        timezone="UTC",
        preferred_currency="RUB",
        status=UserStatus.ACTIVE,
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def _make_workspace(
    db_session: AsyncSession,
    *,
    owner: User,
    slug: str,
    name: str = "WS",
) -> Workspace:
    ws = Workspace(
        owner_id=owner.id,
        name=name,
        slug=slug,
        type=WorkspaceType.SOLO,
        preferred_currency="RUB",
    )
    db_session.add(ws)
    await db_session.flush()
    member = WorkspaceMember(
        workspace_id=ws.id,
        user_id=owner.id,
        role=WorkspaceMemberRole.OWNER,
        brand_ids=[],
        invited_by=None,
    )
    db_session.add(member)
    await db_session.flush()
    return ws


async def _make_brand(db_session: AsyncSession, *, workspace: Workspace) -> Brand:
    brand = Brand(
        workspace_id=workspace.id,
        name="Brand",
        content_language="ru",
        timezone="UTC",
        is_default=True,
    )
    db_session.add(brand)
    await db_session.flush()
    return brand


async def _make_channel(
    db_session: AsyncSession,
    *,
    external_id: int,
    username: str = "test_channel",
    title: str = "Test Channel",
) -> Channel:
    ch = Channel(
        platform="telegram",
        external_id=external_id,
        username=username,
        title=title,
        description="",
        is_public=True,
        subscribers_count=100,
    )
    db_session.add(ch)
    await db_session.flush()
    return ch


async def _make_binding(
    db_session: AsyncSession,
    *,
    workspace: Workspace,
    brand: Brand,
    channel: Channel,
) -> WorkspaceChannel:
    binding = WorkspaceChannel(
        workspace_id=workspace.id,
        brand_id=brand.id,
        channel_id=channel.id,
        role=WorkspaceChannelRoleValues.OWNED,
        bot_admin_rights={
            "status": "administrator",
            "can_post_messages": True,
            "can_edit_messages": True,
            "can_delete_messages": True,
            "captured_at": datetime.now(tz=UTC).isoformat(),
        },
    )
    db_session.add(binding)
    await db_session.flush()
    return binding


@pytest_asyncio.fixture
async def seed(
    db_session: AsyncSession,
) -> tuple[User, Workspace, Brand, Channel, WorkspaceChannel]:
    user = await _make_user(db_session, email="ingest@example.com")
    ws = await _make_workspace(db_session, owner=user, slug="ingest-ws")
    brand = await _make_brand(db_session, workspace=ws)
    channel = await _make_channel(db_session, external_id=-1001234567890)
    binding = await _make_binding(
        db_session,
        workspace=ws,
        brand=brand,
        channel=channel,
    )
    return user, ws, brand, channel, binding


async def _drain_events(redis: fakeredis.aioredis.FakeRedis, channel: str) -> list[dict[str, Any]]:
    """Subscribe + read every pending message on ``channel``.

    Used for asserting the exact event payloads the service emitted
    on the per-user pub/sub channel.
    """

    pubsub = redis.pubsub()
    await pubsub.subscribe(channel)
    received: list[dict[str, Any]] = []
    # Drain after a short loop \u2014 ``fakeredis`` delivers synchronously
    # but ``get_message`` is single-shot.
    while True:
        msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.05)
        if msg is None:
            break
        data = msg["data"]
        if isinstance(data, bytes):
            data = data.decode()
        received.append(json.loads(data))
    await pubsub.unsubscribe(channel)
    await pubsub.close()
    return received


class _Subscriber:
    """Spy that captures published events on per-user channels."""

    def __init__(self, redis: fakeredis.aioredis.FakeRedis) -> None:
        self.redis = redis
        self.received: dict[str, list[dict[str, Any]]] = {}

    async def subscribe(self, *user_ids: uuid.UUID) -> None:
        self._pubsub = self.redis.pubsub()
        self._channels = [user_channel(uid) for uid in user_ids]
        await self._pubsub.subscribe(*self._channels)
        # Drain initial ``subscribe`` confirmation messages so the
        # buffer is clean before the test publishes.
        for _ in range(len(self._channels)):
            await self._pubsub.get_message(timeout=0.05)
        for ch in self._channels:
            self.received[ch] = []

    async def drain(self) -> None:
        for _ in range(20):
            msg = await self._pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=0.05,
            )
            if msg is None:
                break
            data = msg["data"]
            if isinstance(data, bytes):
                data = data.decode()
            self.received[
                msg["channel"].decode() if isinstance(msg["channel"], bytes) else msg["channel"]
            ].append(
                json.loads(data),
            )

    async def close(self) -> None:
        await self._pubsub.unsubscribe(*self._channels)
        await self._pubsub.close()


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_inserts_new_post_and_publishes_event(
    db_session: AsyncSession,
    seed: tuple[User, Workspace, Brand, Channel, WorkspaceChannel],
) -> None:
    user, _ws, _brand, channel, binding = seed
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    sub = _Subscriber(redis)
    await sub.subscribe(user.id)

    msg = _build_message(chat_id=channel.external_id, message_id=42)
    result = await ingest_live_post(db_session, redis, msg, edited=False)

    assert result.status == "inserted"
    assert result.channel_id == channel.id
    assert result.workspace_channel_count == 1

    rows = (
        (await db_session.execute(select(ChannelPost).where(ChannelPost.channel_id == channel.id)))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].tg_message_id == 42

    await sub.drain()
    events = sub.received[user_channel(user.id)]
    assert len(events) == 1
    payload = events[0]
    assert payload["event_type"] == "channel.post_received"
    assert payload["ingest_source"] == "webhook"
    assert payload["channel_id"] == str(channel.id)
    assert payload["workspace_channel_id"] == str(binding.id)
    assert payload["tg_message_id"] == 42
    await sub.close()


@pytest.mark.asyncio
async def test_ingest_duplicate_tg_message_id_is_noop(
    db_session: AsyncSession,
    seed: tuple[User, Workspace, Brand, Channel, WorkspaceChannel],
) -> None:
    user, _ws, _brand, channel, _binding = seed
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    msg = _build_message(chat_id=channel.external_id, message_id=100)
    first = await ingest_live_post(db_session, redis, msg, edited=False)
    assert first.status == "inserted"

    sub = _Subscriber(redis)
    await sub.subscribe(user.id)
    second = await ingest_live_post(db_session, redis, msg, edited=False)
    assert second.status == "duplicate"

    rows = (
        (await db_session.execute(select(ChannelPost).where(ChannelPost.channel_id == channel.id)))
        .scalars()
        .all()
    )
    assert len(rows) == 1

    await sub.drain()
    assert sub.received[user_channel(user.id)] == []
    await sub.close()


# ---------------------------------------------------------------------------
# unknown channel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_unknown_channel_is_dropped(
    db_session: AsyncSession,
    seed: tuple[User, Workspace, Brand, Channel, WorkspaceChannel],
) -> None:
    user, _ws, _brand, _channel, _binding = seed
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    sub = _Subscriber(redis)
    await sub.subscribe(user.id)

    msg = _build_message(chat_id=-1009999999999, message_id=7)
    result = await ingest_live_post(db_session, redis, msg, edited=False)
    assert result.status == "unknown_channel"
    assert result.channel_id is None

    rows = (await db_session.execute(select(ChannelPost))).scalars().all()
    assert rows == []

    await sub.drain()
    assert sub.received[user_channel(user.id)] == []
    await sub.close()


# ---------------------------------------------------------------------------
# service message / empty
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_skips_service_message(
    db_session: AsyncSession,
    seed: tuple[User, Workspace, Brand, Channel, WorkspaceChannel],
) -> None:
    """A message with no text + no media \u2192 parser returns None,
    service short-circuits with ``status="skipped"``."""

    _user, _ws, _brand, channel, _binding = seed
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    msg = _build_message(chat_id=channel.external_id, message_id=5, text=None)
    result = await ingest_live_post(db_session, redis, msg, edited=False)
    assert result.status == "skipped"

    rows = (await db_session.execute(select(ChannelPost))).scalars().all()
    assert rows == []


# ---------------------------------------------------------------------------
# edit path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_edit_updates_existing_post(
    db_session: AsyncSession,
    seed: tuple[User, Workspace, Brand, Channel, WorkspaceChannel],
) -> None:
    user, _ws, _brand, channel, binding = seed
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    # Seed the original row.
    original = _build_message(
        chat_id=channel.external_id,
        message_id=200,
        text="original",
    )
    first = await ingest_live_post(db_session, redis, original, edited=False)
    assert first.status == "inserted"

    sub = _Subscriber(redis)
    await sub.subscribe(user.id)

    edited_msg = _build_message(
        chat_id=channel.external_id,
        message_id=200,
        text="EDITED",
        edit_date=1_700_000_100,
    )
    result = await ingest_live_post(db_session, redis, edited_msg, edited=True)
    assert result.status == "edited"
    assert result.channel_post_id is not None

    row = await db_session.get(ChannelPost, result.channel_post_id)
    assert row is not None
    assert row.text == "EDITED"

    await sub.drain()
    events = sub.received[user_channel(user.id)]
    assert len(events) == 1
    payload = events[0]
    assert payload["event_type"] == "channel.post_edited"
    assert payload["channel_id"] == str(channel.id)
    assert payload["workspace_channel_id"] == str(binding.id)
    assert payload["tg_message_id"] == 200
    assert "edited_at" in payload
    await sub.close()


@pytest.mark.asyncio
async def test_ingest_edit_for_unknown_message_falls_back_to_insert(
    db_session: AsyncSession,
    seed: tuple[User, Workspace, Brand, Channel, WorkspaceChannel],
) -> None:
    user, _ws, _brand, channel, _binding = seed
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    sub = _Subscriber(redis)
    await sub.subscribe(user.id)

    edited_msg = _build_message(
        chat_id=channel.external_id,
        message_id=300,
        text="late edit",
        edit_date=1_700_000_500,
    )
    result = await ingest_live_post(db_session, redis, edited_msg, edited=True)
    assert result.status == "edited_insert"
    assert result.channel_post_id is not None

    row = await db_session.get(ChannelPost, result.channel_post_id)
    assert row is not None
    assert row.tg_message_id == 300

    await sub.drain()
    events = sub.received[user_channel(user.id)]
    # Fallback path publishes ``channel.post_received`` (the post is
    # new from the DB's perspective).
    assert len(events) == 1
    assert events[0]["event_type"] == "channel.post_received"
    assert events[0]["ingest_source"] == "webhook"
    await sub.close()


# ---------------------------------------------------------------------------
# RLS / multi-workspace fan-out
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_does_not_leak_to_unconnected_workspace(
    db_session: AsyncSession,
    seed: tuple[User, Workspace, Brand, Channel, WorkspaceChannel],
) -> None:
    """Workspace B is connected to channel B; ingesting a post in
    channel A must not fan out to workspace B's user."""

    user_a, _ws_a, _brand_a, channel_a, _bind_a = seed

    user_b = await _make_user(db_session, email="b@example.com")
    ws_b = await _make_workspace(db_session, owner=user_b, slug="ws-b")
    brand_b = await _make_brand(db_session, workspace=ws_b)
    channel_b = await _make_channel(
        db_session,
        external_id=-1009876543210,
        username="other_channel",
    )
    await _make_binding(
        db_session,
        workspace=ws_b,
        brand=brand_b,
        channel=channel_b,
    )

    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    sub = _Subscriber(redis)
    await sub.subscribe(user_a.id, user_b.id)

    msg_a = _build_message(chat_id=channel_a.external_id, message_id=11, text="A1")
    result = await ingest_live_post(db_session, redis, msg_a, edited=False)
    assert result.status == "inserted"

    await sub.drain()
    assert len(sub.received[user_channel(user_a.id)]) == 1
    assert sub.received[user_channel(user_b.id)] == []
    await sub.close()


@pytest.mark.asyncio
async def test_ingest_fans_out_to_every_workspace_owning_channel(
    db_session: AsyncSession,
    seed: tuple[User, Workspace, Brand, Channel, WorkspaceChannel],
) -> None:
    """Channel C is connected from workspace A and workspace B.
    Both owners must receive the ``channel.post_received`` event."""

    user_a, _ws_a, _brand_a, channel, _bind_a = seed

    user_b = await _make_user(db_session, email="b2@example.com")
    ws_b = await _make_workspace(db_session, owner=user_b, slug="ws-b2")
    brand_b = await _make_brand(db_session, workspace=ws_b)
    await _make_binding(
        db_session,
        workspace=ws_b,
        brand=brand_b,
        channel=channel,
    )

    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    sub = _Subscriber(redis)
    await sub.subscribe(user_a.id, user_b.id)

    msg = _build_message(chat_id=channel.external_id, message_id=50, text="shared")
    result = await ingest_live_post(db_session, redis, msg, edited=False)
    assert result.status == "inserted"
    assert result.workspace_channel_count == 2

    await sub.drain()
    assert len(sub.received[user_channel(user_a.id)]) == 1
    assert len(sub.received[user_channel(user_b.id)]) == 1
    a_payload = sub.received[user_channel(user_a.id)][0]
    b_payload = sub.received[user_channel(user_b.id)][0]
    # Same channel + post but distinct ``user_id``.
    assert a_payload["channel_id"] == b_payload["channel_id"]
    assert a_payload["channel_post_id"] == b_payload["channel_post_id"]
    assert a_payload["user_id"] != b_payload["user_id"]
    await sub.close()


@pytest.mark.asyncio
async def test_ingest_skips_detached_bindings(
    db_session: AsyncSession,
    seed: tuple[User, Workspace, Brand, Channel, WorkspaceChannel],
) -> None:
    """A soft-detached binding must NOT receive live events."""

    user, _ws, _brand, channel, binding = seed
    binding.disconnected_at = datetime.now(tz=UTC)
    await db_session.flush()

    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    sub = _Subscriber(redis)
    await sub.subscribe(user.id)

    msg = _build_message(chat_id=channel.external_id, message_id=700, text="hi")
    result = await ingest_live_post(db_session, redis, msg, edited=False)
    # The post is still inserted (channel exists), but no binding
    # fan-out happens.
    assert result.status == "inserted"
    assert result.workspace_channel_count == 0

    await sub.drain()
    assert sub.received[user_channel(user.id)] == []
    await sub.close()


@pytest.mark.asyncio
async def test_ingest_uses_edit_date_for_edited_at(
    db_session: AsyncSession,
    seed: tuple[User, Workspace, Brand, Channel, WorkspaceChannel],
) -> None:
    user, _ws, _brand, channel, _binding = seed
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    original = _build_message(
        chat_id=channel.external_id,
        message_id=42,
        text="v1",
    )
    await ingest_live_post(db_session, redis, original, edited=False)

    sub = _Subscriber(redis)
    await sub.subscribe(user.id)

    edit_unix = 1_700_000_777
    edited = _build_message(
        chat_id=channel.external_id,
        message_id=42,
        text="v2",
        edit_date=edit_unix,
    )
    result = await ingest_live_post(db_session, redis, edited, edited=True)
    assert result.status == "edited"

    await sub.drain()
    events = sub.received[user_channel(user.id)]
    assert len(events) == 1
    edited_at_iso = events[0]["edited_at"]
    parsed = datetime.fromisoformat(edited_at_iso.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    assert parsed == datetime.fromtimestamp(edit_unix, tz=UTC)
    await sub.close()


@pytest.mark.asyncio
async def test_live_ingest_result_dataclass_defaults() -> None:
    """``LiveIngestResult`` instances are frozen + carry sane defaults."""

    res = LiveIngestResult(status="skipped")
    assert res.status == "skipped"
    assert res.channel_post_id is None
    assert res.channel_id is None
    assert res.workspace_channel_count == 0
    # ``frozen`` semantics: re-assignment raises.
    with pytest.raises(Exception):
        res.status = "x"  # type: ignore[misc]


# Silence the unused-import linter for ``timedelta`` (kept for future
# tests that exercise scheduled edits).
_ = timedelta
