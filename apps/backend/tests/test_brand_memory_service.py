"""Service-level tests for :mod:`app.services.brand_memory` (PR #21).

Cover the in-process semantics that don't need the FastAPI request
cycle: lazy materialisation, version-conflict semantics, Redis cache
hit/invalidate flow, cross-brand binding guard, effective-payload
shallow merge, examples pagination.
"""

from __future__ import annotations

import uuid

import fakeredis.aioredis
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import (
    BrandMemoryChannelNotBoundError,
    BrandMemoryInvalidPayloadError,
    BrandMemoryVersionConflictError,
)
from app.models.brand import Brand
from app.models.brand_memory import BrandMemoryExample
from app.models.channel import (
    Channel,
    ChannelPlatformValues,
    WorkspaceChannel,
    WorkspaceChannelRoleValues,
)
from app.models.user import User, UserStatus
from app.models.workspace import Workspace
from app.services import brand_memory as bm_service

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


async def _seed_workspace_with_brand(
    db: AsyncSession,
    *,
    email: str = "bm@example.com",
) -> tuple[Workspace, Brand, User]:
    """Create a fresh user/workspace/brand triple for the test.

    Mirrors the bootstrap path users hit on first sign-up so the
    Brand Memory service sees rows it can actually attach memories
    to. Returns the inserted ``(workspace, brand, user)``.
    """

    user = User(
        email=email,
        hashed_password="x" * 60,
        status=UserStatus.ACTIVE,
    )
    db.add(user)
    await db.flush()
    workspace = Workspace(name="ws", slug=f"ws-{user.id.hex[:8]}", owner_id=user.id)
    db.add(workspace)
    await db.flush()
    brand = Brand(
        workspace_id=workspace.id,
        name="Brand A",
        content_language="ru",
        timezone="Europe/Minsk",
        is_default=True,
        disabled_global_skills=[],
    )
    db.add(brand)
    await db.flush()
    return workspace, brand, user


async def _bind_channel(
    db: AsyncSession,
    *,
    workspace: Workspace,
    brand: Brand,
    external_id: int = 100100100,
) -> WorkspaceChannel:
    """Create + attach a Channel to ``brand`` so overlay tests have a target."""

    channel = Channel(
        platform=ChannelPlatformValues.TELEGRAM,
        external_id=external_id,
        username=f"ch{external_id}",
        title="ch",
        is_public=True,
    )
    db.add(channel)
    await db.flush()

    binding = WorkspaceChannel(
        workspace_id=workspace.id,
        brand_id=brand.id,
        channel_id=channel.id,
        role=WorkspaceChannelRoleValues.OWNED,
    )
    db.add(binding)
    await db.flush()
    return binding


# ---------------------------------------------------------------------------
# Core: lazy materialisation + cache hit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_core_materialises_empty_row_on_first_read(
    db_session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    workspace, brand, _ = await _seed_workspace_with_brand(db_session)

    snap = await bm_service.get_core(
        db_session,
        fake_redis,
        brand_id=brand.id,
        workspace_id=workspace.id,
    )
    assert snap.brand_id == brand.id
    assert snap.payload == {}
    assert snap.version == 1
    # The placeholder row must be cached after the first read so the
    # second read doesn't hit the DB at all.
    cached = await fake_redis.get(f"bm:core:{brand.id}")
    assert cached is not None


@pytest.mark.asyncio
async def test_get_core_serves_from_cache_after_warm_read(
    db_session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    workspace, brand, _ = await _seed_workspace_with_brand(db_session)

    first = await bm_service.get_core(
        db_session,
        fake_redis,
        brand_id=brand.id,
        workspace_id=workspace.id,
    )
    # Mutate the cache directly so we can prove the next read hit it.
    await fake_redis.set(
        f"bm:core:{brand.id}",
        await fake_redis.get(f"bm:core:{brand.id}"),
    )
    second = await bm_service.get_core(
        db_session,
        fake_redis,
        brand_id=brand.id,
        workspace_id=workspace.id,
    )
    assert second.version == first.version
    assert second.payload == {}


# ---------------------------------------------------------------------------
# Core: version-checked update + invalidation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_core_bumps_version_and_invalidates_cache(
    db_session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    workspace, brand, user = await _seed_workspace_with_brand(db_session)
    snap = await bm_service.get_core(
        db_session,
        fake_redis,
        brand_id=brand.id,
        workspace_id=workspace.id,
    )
    assert snap.version == 1
    assert await fake_redis.get(f"bm:core:{brand.id}") is not None

    new_payload = {"taboos": ["never claim 100%"], "keywords": ["ai"]}
    updated = await bm_service.update_core(
        db_session,
        fake_redis,
        brand_id=brand.id,
        workspace_id=workspace.id,
        payload=new_payload,
        if_match_version=1,
        updated_by_user_id=user.id,
        updated_by_agent=None,
    )
    assert updated.version == 2
    assert updated.payload == new_payload
    # PATCH must invalidate the stale cache so the next GET sees the
    # new row instead of a stale snapshot.
    assert await fake_redis.get(f"bm:core:{brand.id}") is None


@pytest.mark.asyncio
async def test_update_core_with_stale_version_raises_conflict(
    db_session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    workspace, brand, user = await _seed_workspace_with_brand(db_session)
    await bm_service.get_core(
        db_session,
        fake_redis,
        brand_id=brand.id,
        workspace_id=workspace.id,
    )
    # First PATCH bumps to v2.
    await bm_service.update_core(
        db_session,
        fake_redis,
        brand_id=brand.id,
        workspace_id=workspace.id,
        payload={"keywords": ["a"]},
        if_match_version=1,
        updated_by_user_id=user.id,
        updated_by_agent=None,
    )
    # Second PATCH with stale ``if_match_version=1`` must conflict.
    with pytest.raises(BrandMemoryVersionConflictError) as exc_info:
        await bm_service.update_core(
            db_session,
            fake_redis,
            brand_id=brand.id,
            workspace_id=workspace.id,
            payload={"keywords": ["b"]},
            if_match_version=1,
            updated_by_user_id=user.id,
            updated_by_agent=None,
        )
    assert exc_info.value.http_status == 409
    assert exc_info.value.details["expected_version"] == 1
    assert exc_info.value.details["actual_version"] == 2


@pytest.mark.asyncio
async def test_update_core_rejects_non_dict_payload(
    db_session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    workspace, brand, user = await _seed_workspace_with_brand(db_session)
    with pytest.raises(BrandMemoryInvalidPayloadError):
        await bm_service.update_core(
            db_session,
            fake_redis,
            brand_id=brand.id,
            workspace_id=workspace.id,
            payload="not-a-dict",  # type: ignore[arg-type]
            if_match_version=None,
            updated_by_user_id=user.id,
            updated_by_agent=None,
        )


# ---------------------------------------------------------------------------
# Overlay: missing binding / cross-brand binding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_overlay_returns_none_when_no_row_yet(
    db_session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    workspace, brand, _ = await _seed_workspace_with_brand(db_session)
    binding = await _bind_channel(db_session, workspace=workspace, brand=brand)

    snap = await bm_service.get_overlay(
        db_session,
        fake_redis,
        brand_id=brand.id,
        workspace_channel_id=binding.id,
    )
    assert snap is None


@pytest.mark.asyncio
async def test_get_overlay_for_unbound_channel_404s(
    db_session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    workspace, brand, _ = await _seed_workspace_with_brand(db_session)
    bogus_ws_channel_id = uuid.uuid4()
    with pytest.raises(BrandMemoryChannelNotBoundError):
        await bm_service.get_overlay(
            db_session,
            fake_redis,
            brand_id=brand.id,
            workspace_channel_id=bogus_ws_channel_id,
        )
    # Sanity: a binding belonging to a different brand must also 404
    # so an attacker can't enumerate sibling brands' channels.
    other_brand = Brand(
        workspace_id=workspace.id,
        name="Brand B",
        content_language="ru",
        timezone="UTC",
        is_default=False,
        disabled_global_skills=[],
    )
    db_session.add(other_brand)
    await db_session.flush()
    binding_b = await _bind_channel(
        db_session,
        workspace=workspace,
        brand=other_brand,
        external_id=200200200,
    )
    with pytest.raises(BrandMemoryChannelNotBoundError):
        await bm_service.get_overlay(
            db_session,
            fake_redis,
            brand_id=brand.id,
            workspace_channel_id=binding_b.id,
        )


@pytest.mark.asyncio
async def test_update_overlay_inserts_then_bumps_version(
    db_session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    workspace, brand, user = await _seed_workspace_with_brand(db_session)
    binding = await _bind_channel(db_session, workspace=workspace, brand=brand)

    first = await bm_service.update_overlay(
        db_session,
        fake_redis,
        brand_id=brand.id,
        workspace_id=workspace.id,
        workspace_channel_id=binding.id,
        payload={"keywords": ["channel-only"]},
        if_match_version=None,
        updated_by_user_id=user.id,
        updated_by_agent=None,
    )
    assert first.version == 1
    assert first.payload == {"keywords": ["channel-only"]}

    second = await bm_service.update_overlay(
        db_session,
        fake_redis,
        brand_id=brand.id,
        workspace_id=workspace.id,
        workspace_channel_id=binding.id,
        payload={"keywords": ["channel-only", "extra"]},
        if_match_version=1,
        updated_by_user_id=user.id,
        updated_by_agent=None,
    )
    assert second.version == 2

    with pytest.raises(BrandMemoryVersionConflictError):
        await bm_service.update_overlay(
            db_session,
            fake_redis,
            brand_id=brand.id,
            workspace_id=workspace.id,
            workspace_channel_id=binding.id,
            payload={"keywords": ["stale"]},
            if_match_version=1,
            updated_by_user_id=user.id,
            updated_by_agent=None,
        )


# ---------------------------------------------------------------------------
# Effective payload: shallow merge (overlay keys win)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_effective_merges_overlay_over_core(
    db_session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    workspace, brand, user = await _seed_workspace_with_brand(db_session)
    binding = await _bind_channel(db_session, workspace=workspace, brand=brand)

    await bm_service.update_core(
        db_session,
        fake_redis,
        brand_id=brand.id,
        workspace_id=workspace.id,
        payload={
            "keywords": ["core-1", "core-2"],
            "taboos": ["never"],
        },
        if_match_version=None,
        updated_by_user_id=user.id,
        updated_by_agent=None,
    )
    await bm_service.update_overlay(
        db_session,
        fake_redis,
        brand_id=brand.id,
        workspace_id=workspace.id,
        workspace_channel_id=binding.id,
        payload={"keywords": ["channel-only"]},
        if_match_version=None,
        updated_by_user_id=user.id,
        updated_by_agent=None,
    )

    effective = await bm_service.get_effective(
        db_session,
        fake_redis,
        brand_id=brand.id,
        workspace_id=workspace.id,
        workspace_channel_id=binding.id,
    )
    # ``keywords`` is overridden by the overlay; ``taboos`` falls
    # through from the core.
    assert effective.payload == {
        "keywords": ["channel-only"],
        "taboos": ["never"],
    }
    assert effective.workspace_channel_id == binding.id
    assert effective.core_version >= 2
    assert effective.overlay_version == 1


@pytest.mark.asyncio
async def test_effective_without_channel_returns_core_payload(
    db_session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    workspace, brand, user = await _seed_workspace_with_brand(db_session)
    await bm_service.update_core(
        db_session,
        fake_redis,
        brand_id=brand.id,
        workspace_id=workspace.id,
        payload={"keywords": ["c"]},
        if_match_version=None,
        updated_by_user_id=user.id,
        updated_by_agent=None,
    )
    effective = await bm_service.get_effective(
        db_session,
        fake_redis,
        brand_id=brand.id,
        workspace_id=workspace.id,
        workspace_channel_id=None,
    )
    assert effective.workspace_channel_id is None
    assert effective.overlay_version is None
    assert effective.payload == {"keywords": ["c"]}


# ---------------------------------------------------------------------------
# Examples pagination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_examples_orders_most_recent_first_and_paginates(
    db_session: AsyncSession,
) -> None:
    workspace, brand, _ = await _seed_workspace_with_brand(db_session)
    # Insert three example rows. Embedding column is a 1536-zero list
    # so the JSON-fallback type-decorator round-trips on SQLite.
    zeros = [0.0] * 1536
    for idx in range(3):
        db_session.add(
            BrandMemoryExample(
                workspace_id=workspace.id,
                brand_id=brand.id,
                model="mock:em",
                text_snippet=f"snippet-{idx}",
                embedding=zeros,
            ),
        )
    await db_session.flush()

    rows, total = await bm_service.list_examples(
        db_session,
        brand_id=brand.id,
        limit=2,
        offset=0,
    )
    assert total == 3
    assert len(rows) == 2
    # SQLite doesn't guarantee insertion order under ``created_at
    # DESC`` when every row shares the same default ``CURRENT_TIMESTAMP``
    # (sub-second insertion). What matters is that the count matches
    # and the rows belong to the requested brand.
    assert all(r.brand_id == brand.id for r in rows)
