"""Unit tests for the channels service layer (PR #14).

Pure DB-level coverage of ``app.services.channels``:

* connect happy path → registry upsert + ``workspace_channels`` row
* duplicate connect → typed 409
* reconnect-after-detach → reuses the row, clears
  ``disconnected_at``
* admin / post-permission failures → typed errors
* registry idempotency across workspaces
* detach + verify

Uses :class:`MockTelegramBotClient` so no Bot API is reached. The
service is tested against the SQLite test schema; the production
Postgres parity is covered by ``backend-postgres`` CI.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.social import (
    ChannelInfo,
    ChatMemberInfo,
    MockTelegramBotClient,
)
from app.errors import (
    BotMissingPostPermissionError,
    BotNotAdminError,
    ChannelAlreadyConnectedError,
    ChannelNotConnectedError,
    ChannelNotFoundError,
    TelegramAPIError,
)
from app.models.brand import Brand
from app.models.channel import Channel
from app.models.user import User, UserStatus
from app.models.workspace import Workspace, WorkspaceType
from app.services import channels as channels_service


async def _seed_workspace(session: AsyncSession) -> tuple[User, Workspace, Brand]:
    """Create one user / workspace / default brand for service-level tests."""

    user = User(
        email="svc@example.com",
        hashed_password="x",
        full_name="Svc Tester",
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


def _admin_member(user_id: int = 42, *, can_post: bool = True) -> ChatMemberInfo:
    return ChatMemberInfo(
        user_id=user_id,
        status="administrator",
        can_post_messages=can_post,
        can_edit_messages=can_post,
        can_delete_messages=can_post,
    )


def _build_mock(
    info: ChannelInfo,
    *,
    member: ChatMemberInfo | None = None,
) -> MockTelegramBotClient:
    member = member or _admin_member()
    mock = MockTelegramBotClient(
        channels_by_id={info.chat_id: info},
        channels_by_username=({info.username: info} if info.username else {}),
        members_by_chat={info.chat_id: [member]},
        me_id=member.user_id,
    )
    return mock


@pytest.fixture
def channel_info() -> ChannelInfo:
    return ChannelInfo(
        chat_id=-1001234567890,
        title="Test Channel",
        username="test_channel",
        description="A test channel",
        is_public=True,
        subscribers_count=100,
    )


@pytest.mark.asyncio
async def test_connect_creates_registry_and_binding(
    db_session: AsyncSession,
    channel_info: ChannelInfo,
) -> None:
    _user, workspace, brand = await _seed_workspace(db_session)
    mock = _build_mock(channel_info)

    binding = await channels_service.connect(
        db_session,
        mock,
        workspace_id=workspace.id,
        brand_id=brand.id,
        identifier="test_channel",
    )

    assert binding.workspace_id == workspace.id
    assert binding.brand_id == brand.id
    assert binding.disconnected_at is None
    snapshot: dict[str, Any] = dict(binding.bot_admin_rights or {})
    assert snapshot["can_post_messages"] is True
    assert snapshot["status"] == "administrator"

    channel = await db_session.get(Channel, binding.channel_id)
    assert channel is not None
    assert channel.platform == "telegram"
    assert channel.external_id == channel_info.chat_id
    assert channel.username == "test_channel"
    assert channel.is_public is True


@pytest.mark.asyncio
async def test_connect_idempotent_registry_across_workspaces(
    db_session: AsyncSession,
    channel_info: ChannelInfo,
) -> None:
    """Two workspaces connecting the same channel share one registry row."""

    _user_a, workspace_a, brand_a = await _seed_workspace(db_session)

    user_b = User(
        email="b@example.com",
        hashed_password="x",
        full_name="B",
        locale="ru-RU",
        timezone="UTC",
        preferred_currency="RUB",
        status=UserStatus.ACTIVE,
    )
    db_session.add(user_b)
    await db_session.flush()
    workspace_b = Workspace(
        owner_id=user_b.id,
        name="WS-B",
        slug="default",
        type=WorkspaceType.SOLO,
        preferred_currency="RUB",
    )
    db_session.add(workspace_b)
    await db_session.flush()
    brand_b = Brand(
        workspace_id=workspace_b.id,
        name="Brand-B",
        content_language="ru",
        timezone="UTC",
        is_default=True,
    )
    db_session.add(brand_b)
    await db_session.flush()

    mock = _build_mock(channel_info)

    binding_a = await channels_service.connect(
        db_session,
        mock,
        workspace_id=workspace_a.id,
        brand_id=brand_a.id,
        identifier="test_channel",
    )
    binding_b = await channels_service.connect(
        db_session,
        mock,
        workspace_id=workspace_b.id,
        brand_id=brand_b.id,
        identifier="test_channel",
    )

    assert binding_a.channel_id == binding_b.channel_id
    channel = await db_session.get(Channel, binding_a.channel_id)
    assert channel is not None


@pytest.mark.asyncio
async def test_connect_duplicate_raises_already_connected(
    db_session: AsyncSession,
    channel_info: ChannelInfo,
) -> None:
    _user, workspace, brand = await _seed_workspace(db_session)
    mock = _build_mock(channel_info)

    await channels_service.connect(
        db_session,
        mock,
        workspace_id=workspace.id,
        brand_id=brand.id,
        identifier="test_channel",
    )
    with pytest.raises(ChannelAlreadyConnectedError):
        await channels_service.connect(
            db_session,
            mock,
            workspace_id=workspace.id,
            brand_id=brand.id,
            identifier="test_channel",
        )


@pytest.mark.asyncio
async def test_reconnect_after_detach_clears_disconnected_at(
    db_session: AsyncSession,
    channel_info: ChannelInfo,
) -> None:
    _user, workspace, brand = await _seed_workspace(db_session)
    mock = _build_mock(channel_info)

    binding = await channels_service.connect(
        db_session,
        mock,
        workspace_id=workspace.id,
        brand_id=brand.id,
        identifier="test_channel",
    )
    await channels_service.detach(db_session, binding)
    assert binding.disconnected_at is not None

    revived = await channels_service.connect(
        db_session,
        mock,
        workspace_id=workspace.id,
        brand_id=brand.id,
        identifier="test_channel",
    )
    assert revived.id == binding.id
    assert revived.disconnected_at is None


@pytest.mark.asyncio
async def test_connect_unknown_channel_raises_not_found(
    db_session: AsyncSession,
) -> None:
    _user, workspace, brand = await _seed_workspace(db_session)
    mock = MockTelegramBotClient()
    with pytest.raises(ChannelNotFoundError):
        await channels_service.connect(
            db_session,
            mock,
            workspace_id=workspace.id,
            brand_id=brand.id,
            identifier="ghost_channel",
        )


@pytest.mark.asyncio
async def test_connect_bot_not_admin_raises_typed_error(
    db_session: AsyncSession,
    channel_info: ChannelInfo,
) -> None:
    _user, workspace, brand = await _seed_workspace(db_session)
    mock = MockTelegramBotClient(
        channels_by_username={channel_info.username or "": channel_info},
        channels_by_id={channel_info.chat_id: channel_info},
        members_by_chat={channel_info.chat_id: []},
    )
    with pytest.raises(BotNotAdminError):
        await channels_service.connect(
            db_session,
            mock,
            workspace_id=workspace.id,
            brand_id=brand.id,
            identifier="test_channel",
        )


@pytest.mark.asyncio
async def test_connect_admin_without_post_permission_raises(
    db_session: AsyncSession,
    channel_info: ChannelInfo,
) -> None:
    _user, workspace, brand = await _seed_workspace(db_session)
    member = _admin_member(can_post=False)
    mock = _build_mock(channel_info, member=member)
    with pytest.raises(BotMissingPostPermissionError):
        await channels_service.connect(
            db_session,
            mock,
            workspace_id=workspace.id,
            brand_id=brand.id,
            identifier="test_channel",
        )


@pytest.mark.asyncio
async def test_connect_creator_status_accepted_without_explicit_post_flag(
    db_session: AsyncSession,
    channel_info: ChannelInfo,
) -> None:
    """Bot API ``creator`` status has every right implicitly — accept it."""

    _user, workspace, brand = await _seed_workspace(db_session)
    member = ChatMemberInfo(user_id=42, status="creator")
    mock = _build_mock(channel_info, member=member)
    binding = await channels_service.connect(
        db_session,
        mock,
        workspace_id=workspace.id,
        brand_id=brand.id,
        identifier="test_channel",
    )
    snapshot: dict[str, Any] = dict(binding.bot_admin_rights or {})
    assert snapshot["status"] == "creator"
    assert snapshot["can_post_messages"] is True


@pytest.mark.asyncio
async def test_connect_transport_error_maps_to_telegram_api_error(
    db_session: AsyncSession,
) -> None:
    _user, workspace, brand = await _seed_workspace(db_session)
    mock = MockTelegramBotClient(raise_transport_error=True)
    with pytest.raises(TelegramAPIError):
        await channels_service.connect(
            db_session,
            mock,
            workspace_id=workspace.id,
            brand_id=brand.id,
            identifier="x",
        )


@pytest.mark.asyncio
async def test_list_for_brand_returns_only_active(
    db_session: AsyncSession,
    channel_info: ChannelInfo,
) -> None:
    _user, workspace, brand = await _seed_workspace(db_session)
    mock = _build_mock(channel_info)
    binding = await channels_service.connect(
        db_session,
        mock,
        workspace_id=workspace.id,
        brand_id=brand.id,
        identifier="test_channel",
    )

    rows, total = await channels_service.list_for_brand(
        db_session, workspace_id=workspace.id, brand_id=brand.id
    )
    assert total == 1
    assert rows[0][0].id == binding.id

    await channels_service.detach(db_session, binding)
    rows, total = await channels_service.list_for_brand(
        db_session, workspace_id=workspace.id, brand_id=brand.id
    )
    assert total == 0

    _rows_all, total_all = await channels_service.list_for_brand(
        db_session,
        workspace_id=workspace.id,
        brand_id=brand.id,
        include_disconnected=True,
    )
    assert total_all == 1


@pytest.mark.asyncio
async def test_detach_already_detached_raises(
    db_session: AsyncSession,
    channel_info: ChannelInfo,
) -> None:
    _user, workspace, brand = await _seed_workspace(db_session)
    mock = _build_mock(channel_info)
    binding = await channels_service.connect(
        db_session,
        mock,
        workspace_id=workspace.id,
        brand_id=brand.id,
        identifier="test_channel",
    )
    await channels_service.detach(db_session, binding)
    with pytest.raises(ChannelNotConnectedError):
        await channels_service.detach(db_session, binding)


@pytest.mark.asyncio
async def test_verify_refreshes_snapshot(
    db_session: AsyncSession,
    channel_info: ChannelInfo,
) -> None:
    _user, workspace, brand = await _seed_workspace(db_session)
    mock = _build_mock(channel_info)
    binding = await channels_service.connect(
        db_session,
        mock,
        workspace_id=workspace.id,
        brand_id=brand.id,
        identifier="test_channel",
    )
    channel = await db_session.get(Channel, binding.channel_id)
    assert channel is not None

    # Flip the bot's rights: now lacks post permission.
    mock.members_by_chat[channel_info.chat_id] = [_admin_member(can_post=False)]
    with pytest.raises(BotMissingPostPermissionError):
        await channels_service.verify(db_session, mock, binding, channel)


@pytest.mark.asyncio
async def test_verify_on_detached_binding_raises(
    db_session: AsyncSession,
    channel_info: ChannelInfo,
) -> None:
    _user, workspace, brand = await _seed_workspace(db_session)
    mock = _build_mock(channel_info)
    binding = await channels_service.connect(
        db_session,
        mock,
        workspace_id=workspace.id,
        brand_id=brand.id,
        identifier="test_channel",
    )
    channel = await db_session.get(Channel, binding.channel_id)
    assert channel is not None
    await channels_service.detach(db_session, binding)
    with pytest.raises(ChannelNotConnectedError):
        await channels_service.verify(db_session, mock, binding, channel)
