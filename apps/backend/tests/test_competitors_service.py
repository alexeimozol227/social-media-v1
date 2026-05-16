"""Tests for :mod:`app.services.competitors` (PR #18)."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.social import ChannelInfo, MockTelegramBotClient
from app.errors import (
    ChannelNotConnectedError,
    CompetitorAlreadyConnectedError,
    CompetitorNotPublicError,
)
from app.models.brand import Brand
from app.models.channel import (
    Channel,
    WorkspaceChannel,
    WorkspaceChannelRoleValues,
)
from app.models.user import User, UserStatus
from app.models.workspace import Workspace, WorkspaceType
from app.services import competitors as competitors_service


async def _seed_workspace(session: AsyncSession) -> tuple[User, Workspace, Brand]:
    user = User(
        email="competitors@example.com",
        hashed_password="x",
        full_name="Test",
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
        slug="default",
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
    return user, workspace, brand


def _public_channel(*, username: str = "competitor_channel") -> ChannelInfo:
    return ChannelInfo(
        chat_id=-1009999999990,
        title="Competitor",
        username=username,
        description="A public competitor",
        is_public=True,
        subscribers_count=5000,
    )


def _build_mock(info: ChannelInfo) -> MockTelegramBotClient:
    return MockTelegramBotClient(
        channels_by_id={info.chat_id: info},
        channels_by_username={info.username or "": info},
    )


@pytest.mark.asyncio
async def test_connect_happy_path(db_session: AsyncSession) -> None:
    _user, workspace, brand = await _seed_workspace(db_session)
    info = _public_channel()
    mock = _build_mock(info)
    binding = await competitors_service.connect_competitor(
        db_session,
        mock,
        workspace_id=workspace.id,
        brand_id=brand.id,
        identifier="competitor_channel",
    )
    assert binding.role == WorkspaceChannelRoleValues.COMPETITOR
    assert binding.bot_admin_rights == {}
    assert binding.disconnected_at is None
    channel = await db_session.get(Channel, binding.channel_id)
    assert channel is not None
    assert channel.is_public is True


@pytest.mark.asyncio
async def test_connect_rejects_private_channel(db_session: AsyncSession) -> None:
    _user, workspace, brand = await _seed_workspace(db_session)
    info = ChannelInfo(
        chat_id=-1001234,
        title="Private",
        username=None,
        description=None,
        is_public=False,
    )
    mock = MockTelegramBotClient(
        channels_by_id={info.chat_id: info},
        channels_by_username={},
    )
    with pytest.raises(CompetitorNotPublicError):
        await competitors_service.connect_competitor(
            db_session,
            mock,
            workspace_id=workspace.id,
            brand_id=brand.id,
            identifier=info.chat_id,
        )


@pytest.mark.asyncio
async def test_connect_duplicate_raises(db_session: AsyncSession) -> None:
    _user, workspace, brand = await _seed_workspace(db_session)
    info = _public_channel()
    mock = _build_mock(info)
    await competitors_service.connect_competitor(
        db_session,
        mock,
        workspace_id=workspace.id,
        brand_id=brand.id,
        identifier="competitor_channel",
    )
    with pytest.raises(CompetitorAlreadyConnectedError):
        await competitors_service.connect_competitor(
            db_session,
            mock,
            workspace_id=workspace.id,
            brand_id=brand.id,
            identifier="competitor_channel",
        )


@pytest.mark.asyncio
async def test_reconnect_after_detach_lifts_disconnected_at(
    db_session: AsyncSession,
) -> None:
    _user, workspace, brand = await _seed_workspace(db_session)
    info = _public_channel()
    mock = _build_mock(info)
    binding = await competitors_service.connect_competitor(
        db_session,
        mock,
        workspace_id=workspace.id,
        brand_id=brand.id,
        identifier="competitor_channel",
    )
    await competitors_service.detach_competitor(db_session, binding)
    assert binding.disconnected_at is not None

    revived = await competitors_service.connect_competitor(
        db_session,
        mock,
        workspace_id=workspace.id,
        brand_id=brand.id,
        identifier="competitor_channel",
    )
    assert revived.id == binding.id
    assert revived.disconnected_at is None
    assert revived.role == WorkspaceChannelRoleValues.COMPETITOR
    assert revived.bot_admin_rights == {}


@pytest.mark.asyncio
async def test_list_filters_by_competitor_role(db_session: AsyncSession) -> None:
    """Channel rows with role=owned must NOT appear in the competitor list."""

    _user, workspace, brand = await _seed_workspace(db_session)
    competitor_info = _public_channel(username="competitor_a")
    mock_c = _build_mock(competitor_info)
    await competitors_service.connect_competitor(
        db_session,
        mock_c,
        workspace_id=workspace.id,
        brand_id=brand.id,
        identifier="competitor_a",
    )

    # Seed an OWNED-role row inline so we don't have to drive the full
    # channels-service flow (admin probe etc).
    owned_channel = Channel(
        platform="telegram",
        external_id=-1008888888880,
        username="owned_channel",
        title="Owned",
        is_public=True,
    )
    db_session.add(owned_channel)
    await db_session.flush()
    owned_binding = WorkspaceChannel(
        workspace_id=workspace.id,
        brand_id=brand.id,
        channel_id=owned_channel.id,
        role=WorkspaceChannelRoleValues.OWNED,
        bot_admin_rights={"status": "administrator"},
    )
    db_session.add(owned_binding)
    await db_session.flush()

    rows, total = await competitors_service.list_competitors_for_brand(
        db_session,
        workspace_id=workspace.id,
        brand_id=brand.id,
    )
    assert total == 1
    assert rows[0][1].username == "competitor_a"


@pytest.mark.asyncio
async def test_detach_already_detached_raises(db_session: AsyncSession) -> None:
    _user, workspace, brand = await _seed_workspace(db_session)
    info = _public_channel()
    mock = _build_mock(info)
    binding = await competitors_service.connect_competitor(
        db_session,
        mock,
        workspace_id=workspace.id,
        brand_id=brand.id,
        identifier="competitor_channel",
    )
    await competitors_service.detach_competitor(db_session, binding)
    with pytest.raises(ChannelNotConnectedError):
        await competitors_service.detach_competitor(db_session, binding)


@pytest.mark.asyncio
async def test_get_binding_filters_by_competitor_role(
    db_session: AsyncSession,
) -> None:
    """``get_competitor_binding`` returns ``None`` for an OWNED row even if the ids match."""

    _user, workspace, brand = await _seed_workspace(db_session)
    owned_channel = Channel(
        platform="telegram",
        external_id=-1007777777770,
        username="owned_x",
        title="Owned X",
        is_public=True,
    )
    db_session.add(owned_channel)
    await db_session.flush()
    owned_binding = WorkspaceChannel(
        workspace_id=workspace.id,
        brand_id=brand.id,
        channel_id=owned_channel.id,
        role=WorkspaceChannelRoleValues.OWNED,
        bot_admin_rights={"status": "administrator"},
    )
    db_session.add(owned_binding)
    await db_session.flush()

    found = await competitors_service.get_competitor_binding(
        db_session,
        workspace_id=workspace.id,
        brand_id=brand.id,
        workspace_channel_id=owned_binding.id,
    )
    assert found is None
