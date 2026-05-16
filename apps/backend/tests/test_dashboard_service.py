"""Unit tests for :mod:`app.services.dashboard` (PR #19).

Covers the four resolution branches:

* ``ok`` — main channel + at least one post.
* ``no_active_channel`` — brand has no active owned binding.
* ``no_posts_yet`` — channel connected, ingest hasn't surfaced
  anything yet.
* Newest-first ordering when more than ``limit`` posts exist.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.brand import Brand
from app.models.channel import (
    Channel,
    ChannelPlatformValues,
    ChannelPost,
    WorkspaceChannel,
    WorkspaceChannelRoleValues,
)
from app.models.user import User, UserStatus
from app.models.workspace import Workspace, WorkspaceType
from app.services import dashboard as dashboard_service


async def _seed_brand(
    session: AsyncSession,
) -> tuple[Workspace, Brand]:
    user = User(
        email=f"dash-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
        full_name="Dash",
        locale="ru-RU",
        timezone="UTC",
        preferred_currency="RUB",
        status=UserStatus.ACTIVE,
    )
    session.add(user)
    await session.flush()
    workspace = Workspace(
        owner_id=user.id,
        name="WS",
        slug=f"ws-{uuid.uuid4().hex[:8]}",
        type=WorkspaceType.SOLO,
        preferred_currency="RUB",
    )
    session.add(workspace)
    await session.flush()
    brand = Brand(
        workspace_id=workspace.id,
        name="Brand",
        content_language="ru",
        timezone="UTC",
        is_default=True,
    )
    session.add(brand)
    await session.flush()
    return workspace, brand


async def _seed_owned_binding(
    session: AsyncSession,
    *,
    workspace: Workspace,
    brand: Brand,
    chat_id: int = -1009999999991,
    username: str = "owned_channel",
    title: str = "Owned",
) -> tuple[Channel, WorkspaceChannel]:
    channel = Channel(
        platform=ChannelPlatformValues.TELEGRAM,
        external_id=chat_id,
        username=username,
        title=title,
        subscribers_count=1000,
        is_public=True,
    )
    session.add(channel)
    await session.flush()
    binding = WorkspaceChannel(
        workspace_id=workspace.id,
        brand_id=brand.id,
        channel_id=channel.id,
        role=WorkspaceChannelRoleValues.OWNED,
        bot_admin_rights={},
    )
    session.add(binding)
    await session.flush()
    return channel, binding


@pytest.mark.asyncio
async def test_dashboard_no_active_channel(
    db_session: AsyncSession,
) -> None:
    workspace, brand = await _seed_brand(db_session)
    summary = await dashboard_service.get_recent_posts_for_brand(
        db_session,
        workspace_id=workspace.id,
        brand_id=brand.id,
    )
    assert summary.status == "no_active_channel"
    assert summary.channel is None
    assert summary.recent_posts == []


@pytest.mark.asyncio
async def test_dashboard_no_posts_yet(
    db_session: AsyncSession,
) -> None:
    workspace, brand = await _seed_brand(db_session)
    await _seed_owned_binding(db_session, workspace=workspace, brand=brand)
    summary = await dashboard_service.get_recent_posts_for_brand(
        db_session,
        workspace_id=workspace.id,
        brand_id=brand.id,
    )
    assert summary.status == "no_posts_yet"
    assert summary.channel is not None
    assert summary.channel.username == "owned_channel"
    assert summary.recent_posts == []


@pytest.mark.asyncio
async def test_dashboard_returns_recent_posts_newest_first(
    db_session: AsyncSession,
) -> None:
    workspace, brand = await _seed_brand(db_session)
    channel, _ = await _seed_owned_binding(db_session, workspace=workspace, brand=brand)
    base = datetime.now(tz=UTC)
    for i in range(7):
        db_session.add(
            ChannelPost(
                channel_id=channel.id,
                tg_message_id=100 + i,
                text=f"Post #{i}",
                has_media=False,
                posted_at=base + timedelta(minutes=i),
            ),
        )
    await db_session.flush()

    summary = await dashboard_service.get_recent_posts_for_brand(
        db_session,
        workspace_id=workspace.id,
        brand_id=brand.id,
        limit=5,
    )
    assert summary.status == "ok"
    assert summary.channel is not None
    assert len(summary.recent_posts) == 5
    assert [p.tg_message_id for p in summary.recent_posts] == [106, 105, 104, 103, 102]
    # First post in window contains the actual text.
    assert summary.recent_posts[0].text_preview == "Post #6"


@pytest.mark.asyncio
async def test_dashboard_trims_long_text_preview(
    db_session: AsyncSession,
) -> None:
    workspace, brand = await _seed_brand(db_session)
    channel, _ = await _seed_owned_binding(db_session, workspace=workspace, brand=brand)
    long_body = "x" * 500
    db_session.add(
        ChannelPost(
            channel_id=channel.id,
            tg_message_id=200,
            text=long_body,
            has_media=False,
            posted_at=datetime.now(tz=UTC),
        ),
    )
    await db_session.flush()

    summary = await dashboard_service.get_recent_posts_for_brand(
        db_session,
        workspace_id=workspace.id,
        brand_id=brand.id,
        limit=5,
    )
    preview = summary.recent_posts[0].text_preview
    assert preview is not None
    assert len(preview) == dashboard_service.TEXT_PREVIEW_MAX
    assert preview.endswith("…")


@pytest.mark.asyncio
async def test_dashboard_ignores_disconnected_bindings(
    db_session: AsyncSession,
) -> None:
    """A soft-detached binding doesn't power the dashboard."""

    workspace, brand = await _seed_brand(db_session)
    _, binding = await _seed_owned_binding(db_session, workspace=workspace, brand=brand)
    binding.disconnected_at = datetime.now(tz=UTC)
    await db_session.flush()

    summary = await dashboard_service.get_recent_posts_for_brand(
        db_session,
        workspace_id=workspace.id,
        brand_id=brand.id,
    )
    assert summary.status == "no_active_channel"


@pytest.mark.asyncio
async def test_dashboard_resolves_oldest_owned_binding(
    db_session: AsyncSession,
) -> None:
    """When multiple owned bindings exist, the oldest one wins (stable)."""

    workspace, brand = await _seed_brand(db_session)
    older_channel, _ = await _seed_owned_binding(
        db_session,
        workspace=workspace,
        brand=brand,
        chat_id=-1009999999991,
        username="older",
    )
    _, newer_binding = await _seed_owned_binding(
        db_session,
        workspace=workspace,
        brand=brand,
        chat_id=-1009999999992,
        username="newer",
    )
    # Force newer.connected_at strictly after older.connected_at.
    newer_binding.connected_at = datetime.now(tz=UTC) + timedelta(minutes=5)
    await db_session.flush()

    db_session.add(
        ChannelPost(
            channel_id=older_channel.id,
            tg_message_id=500,
            text="hello",
            has_media=False,
            posted_at=datetime.now(tz=UTC),
        ),
    )
    await db_session.flush()

    summary = await dashboard_service.get_recent_posts_for_brand(
        db_session,
        workspace_id=workspace.id,
        brand_id=brand.id,
    )
    assert summary.status == "ok"
    assert summary.channel is not None
    assert summary.channel.username == "older"


@pytest.mark.asyncio
async def test_dashboard_rejects_non_positive_limit(
    db_session: AsyncSession,
) -> None:
    workspace, brand = await _seed_brand(db_session)
    with pytest.raises(ValueError, match="positive"):
        await dashboard_service.get_recent_posts_for_brand(
            db_session,
            workspace_id=workspace.id,
            brand_id=brand.id,
            limit=0,
        )
