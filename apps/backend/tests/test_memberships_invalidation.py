"""Role-mutation → membership cache invalidation + WS push (PR #12).

docs/04 §18.6 + docs/05 §4.5 + docs/06 §5 Спринт 1: every
``workspace_members`` mutation must:

* drop the affected user's Redis ``user:{id}:memberships`` cache entry, and
* publish :class:`app.events.schemas.AuthRefreshRequiredEvent` on
  the user's per-user WS channel so the SPA refreshes their access
  token immediately.

These tests subscribe to the per-user Redis channel via fakeredis +
verify both side-effects atomically.
"""

from __future__ import annotations

import asyncio
import json
import uuid

import fakeredis.aioredis
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.event_bus import user_channel
from app.models.user import PlatformRole, User, UserStatus
from app.models.workspace import Workspace, WorkspaceType
from app.models.workspace_member import WorkspaceMember, WorkspaceMemberRole
from app.services import memberships, memberships_cache


async def _seed_member(
    db_session: AsyncSession,
    *,
    role: str = WorkspaceMemberRole.EDITOR,
) -> tuple[User, Workspace, WorkspaceMember]:
    """Seed a user + workspace + workspace_member triple for the tests."""

    owner = User(
        email=f"owner-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
        status=UserStatus.ACTIVE,
        platform_role=PlatformRole.USER,
    )
    member = User(
        email=f"member-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
        status=UserStatus.ACTIVE,
        platform_role=PlatformRole.USER,
    )
    db_session.add_all([owner, member])
    await db_session.flush()
    workspace = Workspace(
        owner_id=owner.id,
        name="WS",
        slug=f"ws-{uuid.uuid4().hex[:8]}",
        type=WorkspaceType.SOLO,
        preferred_currency="RUB",
    )
    db_session.add(workspace)
    await db_session.flush()
    membership = WorkspaceMember(
        workspace_id=workspace.id,
        user_id=member.id,
        role=role,
    )
    db_session.add(membership)
    await db_session.flush()
    return member, workspace, membership


async def _drain_one_event(
    pubsub: fakeredis.aioredis.FakeRedis,
    *,
    timeout: float = 1.0,
) -> dict[str, object] | None:
    """Read a single non-subscribe frame off ``pubsub``.

    Returns the parsed JSON payload, or ``None`` on timeout.
    """

    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        msg = await pubsub.get_message(
            ignore_subscribe_messages=True,
            timeout=0.1,
        )
        if msg is not None:
            data = msg.get("data") if isinstance(msg, dict) else None
            if isinstance(data, bytes):
                data = data.decode("utf-8")
            if isinstance(data, str):
                return json.loads(data)
    return None


@pytest.mark.asyncio
async def test_set_role_busts_cache_and_pushes_auth_refresh_required(
    db_session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """Role change → cache DEL + WS push ``auth.refresh_required``."""

    member, workspace, _ = await _seed_member(db_session)

    # Prime the cache so we can assert the DEL hit it.
    await memberships_cache.get_memberships(fake_redis, db_session, member.id)
    assert await fake_redis.get(memberships_cache.cache_key(member.id)) is not None

    pubsub = fake_redis.pubsub()
    await pubsub.subscribe(user_channel(member.id))
    try:
        await memberships.set_role(
            db_session,
            fake_redis,
            workspace_id=workspace.id,
            user_id=member.id,
            role=WorkspaceMemberRole.ADMIN,
        )

        assert await fake_redis.get(memberships_cache.cache_key(member.id)) is None

        event = await _drain_one_event(pubsub)
    finally:
        await pubsub.unsubscribe(user_channel(member.id))
        await pubsub.close()

    assert event is not None, "expected auth.refresh_required event"
    assert event["event_type"] == "auth.refresh_required"
    assert event["agent_source"] == "platform.auth"
    assert event["user_id"] == str(member.id)
    assert event["workspace_id"] == str(workspace.id)
    assert event["reason"] == "role_changed"


@pytest.mark.asyncio
async def test_remove_busts_cache_and_pushes_invite_revoked(
    db_session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    member, workspace, _ = await _seed_member(db_session)
    await memberships_cache.get_memberships(fake_redis, db_session, member.id)

    pubsub = fake_redis.pubsub()
    await pubsub.subscribe(user_channel(member.id))
    try:
        deleted = await memberships.remove(
            db_session,
            fake_redis,
            workspace_id=workspace.id,
            user_id=member.id,
        )
        assert deleted is True

        # Cache is gone, so the next read re-primes against the empty set.
        assert await fake_redis.get(memberships_cache.cache_key(member.id)) is None

        event = await _drain_one_event(pubsub)
    finally:
        await pubsub.unsubscribe(user_channel(member.id))
        await pubsub.close()

    assert event is not None
    assert event["event_type"] == "auth.refresh_required"
    assert event["reason"] == "invite_revoked"


@pytest.mark.asyncio
async def test_add_busts_cache_and_pushes_invited(
    db_session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    owner = User(
        email=f"o-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
        status=UserStatus.ACTIVE,
        platform_role=PlatformRole.USER,
    )
    invitee = User(
        email=f"i-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
        status=UserStatus.ACTIVE,
        platform_role=PlatformRole.USER,
    )
    db_session.add_all([owner, invitee])
    await db_session.flush()
    workspace = Workspace(
        owner_id=owner.id,
        name="WS",
        slug=f"ws-{uuid.uuid4().hex[:8]}",
        type=WorkspaceType.AGENCY,
        preferred_currency="RUB",
    )
    db_session.add(workspace)
    await db_session.flush()

    pubsub = fake_redis.pubsub()
    await pubsub.subscribe(user_channel(invitee.id))
    try:
        await memberships.add(
            db_session,
            fake_redis,
            workspace_id=workspace.id,
            user_id=invitee.id,
            role=WorkspaceMemberRole.EDITOR,
            invited_by=owner.id,
        )

        event = await _drain_one_event(pubsub)
    finally:
        await pubsub.unsubscribe(user_channel(invitee.id))
        await pubsub.close()

    assert event is not None
    assert event["reason"] == "invited"


@pytest.mark.asyncio
async def test_set_role_on_missing_member_raises(
    db_session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """Editing a non-existent member is a hard error — admin UI must
    invite first."""

    with pytest.raises(LookupError):
        await memberships.set_role(
            db_session,
            fake_redis,
            workspace_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            role=WorkspaceMemberRole.ADMIN,
        )
