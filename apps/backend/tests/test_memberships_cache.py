"""Redis ``user:{id}:memberships`` cache (PR #12 / D64).

docs/04 §18.6 + docs/05 §4.5 + docs/06 §5 Спринт 1: membership cache
is read on every authenticated request, populated on first miss
from ``workspace_members``, and invalidated by the role-mutation
service plus a WS push.

These tests pin the contract end-to-end without standing up a real
Redis server (``fakeredis.aioredis.FakeRedis`` is what production
uses everywhere else in the suite).
"""

from __future__ import annotations

import json
import uuid

import fakeredis.aioredis
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import PlatformRole, User, UserStatus
from app.models.workspace import Workspace, WorkspaceType
from app.models.workspace_member import WorkspaceMember, WorkspaceMemberRole
from app.services import memberships_cache


@pytest.mark.asyncio
async def test_cache_key_is_namespaced_per_user() -> None:
    """Key is ``user:{user_id}:memberships`` per docs/04 §18.6."""

    user_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    assert memberships_cache.cache_key(user_id) == (
        "user:11111111-1111-1111-1111-111111111111:memberships"
    )


@pytest.mark.asyncio
async def test_get_memberships_cache_miss_loads_from_db_and_seeds_redis(
    db_session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """First read: miss → SELECT → prime entry with the TTL from D64."""

    user = User(
        email="m1@example.com",
        hashed_password="x",
        status=UserStatus.ACTIVE,
        platform_role=PlatformRole.USER,
    )
    db_session.add(user)
    await db_session.flush()
    workspace = Workspace(
        owner_id=user.id,
        name="WS",
        slug="default",
        type=WorkspaceType.SOLO,
        preferred_currency="RUB",
    )
    db_session.add(workspace)
    await db_session.flush()
    db_session.add(
        WorkspaceMember(
            workspace_id=workspace.id,
            user_id=user.id,
            role=WorkspaceMemberRole.OWNER,
        ),
    )
    await db_session.flush()

    out = await memberships_cache.get_memberships(fake_redis, db_session, user.id)
    assert len(out) == 1
    assert out[0]["workspace_id"] == str(workspace.id)
    assert out[0]["role"] == WorkspaceMemberRole.OWNER

    raw = await fake_redis.get(memberships_cache.cache_key(user.id))
    assert raw is not None
    parsed = json.loads(raw)
    assert parsed == [
        {
            "workspace_id": str(workspace.id),
            "role": WorkspaceMemberRole.OWNER,
            "brand_ids": None,
        },
    ]
    ttl = await fake_redis.ttl(memberships_cache.cache_key(user.id))
    # ``ttl`` returns -2 / -1 for missing / no-expiry — neither is acceptable.
    assert 0 < ttl <= memberships_cache.MEMBERSHIP_CACHE_TTL_SECONDS


@pytest.mark.asyncio
async def test_get_memberships_serves_cached_value_without_db(
    db_session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """Hot cache short-circuits the DB even if the row was since deleted."""

    user = User(
        email="m2@example.com",
        hashed_password="x",
        status=UserStatus.ACTIVE,
        platform_role=PlatformRole.USER,
    )
    db_session.add(user)
    await db_session.flush()

    cached_workspace_id = "22222222-2222-2222-2222-222222222222"
    await fake_redis.set(
        memberships_cache.cache_key(user.id),
        json.dumps(
            [
                {
                    "workspace_id": cached_workspace_id,
                    "role": WorkspaceMemberRole.EDITOR,
                    "brand_ids": None,
                },
            ],
        ),
        ex=memberships_cache.MEMBERSHIP_CACHE_TTL_SECONDS,
    )

    out = await memberships_cache.get_memberships(fake_redis, db_session, user.id)
    assert out == [
        {
            "workspace_id": cached_workspace_id,
            "role": WorkspaceMemberRole.EDITOR,
            "brand_ids": None,
        },
    ]


@pytest.mark.asyncio
async def test_invalidate_drops_cached_entry(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    user_id = uuid.uuid4()
    await fake_redis.set(
        memberships_cache.cache_key(user_id),
        json.dumps([{"workspace_id": "x", "role": "owner", "brand_ids": None}]),
    )

    await memberships_cache.invalidate(fake_redis, user_id)

    assert await fake_redis.get(memberships_cache.cache_key(user_id)) is None


@pytest.mark.asyncio
async def test_empty_membership_list_is_negative_cached(
    db_session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """A user without any workspaces gets ``[]`` cached so the next
    request doesn't pay another SELECT (D64 negative-cache)."""

    user = User(
        email="m3@example.com",
        hashed_password="x",
        status=UserStatus.ACTIVE,
        platform_role=PlatformRole.USER,
    )
    db_session.add(user)
    await db_session.flush()

    out = await memberships_cache.get_memberships(fake_redis, db_session, user.id)
    assert out == []

    raw = await fake_redis.get(memberships_cache.cache_key(user.id))
    assert raw == "[]"
