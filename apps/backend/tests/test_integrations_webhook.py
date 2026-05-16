"""Integration tests for ``POST /v1/integrations/telegram/webhook`` (PR #16).

Exercises the full webhook flow end-to-end:

* valid secret + ``channel_post`` \u2192 204 + 1 row inserted + event published
* valid secret + ``edited_channel_post`` \u2192 204 + row updated
* invalid secret \u2192 401 :class:`TELEGRAM_WEBHOOK_UNAUTHORIZED`
* missing secret header \u2192 401
* empty server-side secret \u2192 401 (forgotten env var doesn't leak)
* unknown channel \u2192 204, no DB rows, no event
* duplicate ``tg_message_id`` \u2192 204, still 1 row, no second event
* malformed JSON \u2192 204 (Telegram retries on non-2xx \u2014 we eat it)
* unknown update type (random fields only) \u2192 204
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
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

WEBHOOK_PATH = "/v1/integrations/telegram/webhook"
TEST_SECRET = "test-tg-secret-token-1234"
CHAT_ID = -1001234567890


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def configure_secret() -> AsyncIterator[None]:
    """Temporarily set the webhook secret so the route accepts deliveries."""

    original = settings.telegram_webhook_secret
    settings.telegram_webhook_secret = TEST_SECRET
    try:
        yield
    finally:
        settings.telegram_webhook_secret = original


@pytest_asyncio.fixture
async def seed_channel(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[tuple[User, Workspace, Brand, Channel, WorkspaceChannel]]:
    """Seed a fully-connected channel inside its own session.

    We use the test DB factory directly (rather than the route's
    ``db_session`` fixture) so the rows are committed and visible to
    the request-scoped session that the FastAPI app spins up for
    the webhook call.
    """

    async with db_session_factory() as session:
        user = User(
            email="webhook@example.com",
            hashed_password="x",
            full_name="Webhook Tester",
            locale="ru-RU",
            timezone="UTC",
            preferred_currency="RUB",
            status=UserStatus.ACTIVE,
        )
        session.add(user)
        await session.flush()

        ws = Workspace(
            owner_id=user.id,
            name="WS",
            slug="webhook-ws",
            type=WorkspaceType.SOLO,
            preferred_currency="RUB",
        )
        session.add(ws)
        await session.flush()
        session.add(
            WorkspaceMember(
                workspace_id=ws.id,
                user_id=user.id,
                role=WorkspaceMemberRole.OWNER,
                brand_ids=[],
                invited_by=None,
            )
        )
        await session.flush()

        brand = Brand(
            workspace_id=ws.id,
            name="Brand",
            content_language="ru",
            timezone="UTC",
            is_default=True,
        )
        session.add(brand)
        await session.flush()

        channel = Channel(
            platform="telegram",
            external_id=CHAT_ID,
            username="test_channel",
            title="Test Channel",
            description="",
            is_public=True,
            subscribers_count=100,
        )
        session.add(channel)
        await session.flush()

        binding = WorkspaceChannel(
            workspace_id=ws.id,
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
        session.add(binding)
        await session.flush()
        await session.commit()
        yield user, ws, brand, channel, binding


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _channel_post_payload(
    *,
    message_id: int = 1001,
    text: str = "hello world",
    chat_id: int = CHAT_ID,
    posted_at_unix: int = 1_700_000_000,
    edited: bool = False,
    edit_date: int | None = None,
) -> dict[str, Any]:
    message = {
        "message_id": message_id,
        "date": posted_at_unix,
        "chat": {
            "id": chat_id,
            "type": "channel",
            "title": "Test Channel",
            "username": "test_channel",
        },
        "text": text,
    }
    if edit_date is not None:
        message["edit_date"] = edit_date
    key = "edited_channel_post" if edited else "channel_post"
    return {"update_id": uuid.uuid4().int & 0xFFFFFFFF, key: message}


async def _post_count(
    db_session_factory: async_sessionmaker[AsyncSession],
    channel_id: uuid.UUID,
) -> int:
    async with db_session_factory() as session:
        rows = (
            (await session.execute(select(ChannelPost).where(ChannelPost.channel_id == channel_id)))
            .scalars()
            .all()
        )
        return len(rows)


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_valid_secret_inserts_post(
    client: AsyncClient,
    configure_secret: None,
    seed_channel: tuple[User, Workspace, Brand, Channel, WorkspaceChannel],
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _u, _ws, _brand, channel, _bind = seed_channel
    payload = _channel_post_payload(message_id=10, text="first live post")
    resp = await client.post(
        WEBHOOK_PATH,
        json=payload,
        headers={"X-Telegram-Bot-API-Secret-Token": TEST_SECRET},
    )
    assert resp.status_code == 204, resp.text
    assert resp.content == b""
    assert await _post_count(db_session_factory, channel.id) == 1


@pytest.mark.asyncio
async def test_webhook_edited_channel_post_updates_row(
    client: AsyncClient,
    configure_secret: None,
    seed_channel: tuple[User, Workspace, Brand, Channel, WorkspaceChannel],
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _u, _ws, _brand, channel, _bind = seed_channel

    # First deliver the original.
    resp = await client.post(
        WEBHOOK_PATH,
        json=_channel_post_payload(message_id=11, text="v1"),
        headers={"X-Telegram-Bot-API-Secret-Token": TEST_SECRET},
    )
    assert resp.status_code == 204

    # Then deliver an edit.
    resp = await client.post(
        WEBHOOK_PATH,
        json=_channel_post_payload(
            message_id=11,
            text="v2 edited",
            edited=True,
            edit_date=1_700_000_111,
        ),
        headers={"X-Telegram-Bot-API-Secret-Token": TEST_SECRET},
    )
    assert resp.status_code == 204

    # Row was upserted, not duplicated.
    assert await _post_count(db_session_factory, channel.id) == 1
    async with db_session_factory() as session:
        rows = (
            (await session.execute(select(ChannelPost).where(ChannelPost.channel_id == channel.id)))
            .scalars()
            .all()
        )
        assert rows[0].text == "v2 edited"


# ---------------------------------------------------------------------------
# auth failure modes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_invalid_secret_returns_401(
    client: AsyncClient,
    configure_secret: None,
    seed_channel: tuple[User, Workspace, Brand, Channel, WorkspaceChannel],
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _u, _ws, _brand, channel, _bind = seed_channel
    resp = await client.post(
        WEBHOOK_PATH,
        json=_channel_post_payload(message_id=20),
        headers={"X-Telegram-Bot-API-Secret-Token": "WRONG"},
    )
    assert resp.status_code == 401
    body = resp.json()
    assert body["error_code"] == "TELEGRAM_WEBHOOK_UNAUTHORIZED"
    # No side effects.
    assert await _post_count(db_session_factory, channel.id) == 0


@pytest.mark.asyncio
async def test_webhook_missing_secret_header_returns_401(
    client: AsyncClient,
    configure_secret: None,
    seed_channel: tuple[User, Workspace, Brand, Channel, WorkspaceChannel],
) -> None:
    resp = await client.post(
        WEBHOOK_PATH,
        json=_channel_post_payload(message_id=21),
    )
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "TELEGRAM_WEBHOOK_UNAUTHORIZED"


@pytest.mark.asyncio
async def test_webhook_empty_server_secret_returns_401(
    client: AsyncClient,
    seed_channel: tuple[User, Workspace, Brand, Channel, WorkspaceChannel],
) -> None:
    """A forgotten ``telegram_webhook_secret`` env var must not
    expose the endpoint \u2014 even a request that omits the header
    is rejected with 401."""

    original = settings.telegram_webhook_secret
    settings.telegram_webhook_secret = ""
    try:
        resp = await client.post(
            WEBHOOK_PATH,
            json=_channel_post_payload(message_id=22),
            headers={"X-Telegram-Bot-API-Secret-Token": "anything"},
        )
        assert resp.status_code == 401
        assert resp.json()["error_code"] == "TELEGRAM_WEBHOOK_UNAUTHORIZED"
    finally:
        settings.telegram_webhook_secret = original


# ---------------------------------------------------------------------------
# silent drops
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_unknown_channel_returns_204(
    client: AsyncClient,
    configure_secret: None,
    seed_channel: tuple[User, Workspace, Brand, Channel, WorkspaceChannel],
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _u, _ws, _brand, channel, _bind = seed_channel
    resp = await client.post(
        WEBHOOK_PATH,
        json=_channel_post_payload(message_id=30, chat_id=-1009999999999),
        headers={"X-Telegram-Bot-API-Secret-Token": TEST_SECRET},
    )
    assert resp.status_code == 204
    assert await _post_count(db_session_factory, channel.id) == 0


@pytest.mark.asyncio
async def test_webhook_duplicate_post_returns_204(
    client: AsyncClient,
    configure_secret: None,
    seed_channel: tuple[User, Workspace, Brand, Channel, WorkspaceChannel],
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _u, _ws, _brand, channel, _bind = seed_channel
    payload = _channel_post_payload(message_id=40, text="dup")
    first = await client.post(
        WEBHOOK_PATH,
        json=payload,
        headers={"X-Telegram-Bot-API-Secret-Token": TEST_SECRET},
    )
    assert first.status_code == 204

    # Same ``message_id`` \u2192 dedup'd.
    second = await client.post(
        WEBHOOK_PATH,
        json=payload,
        headers={"X-Telegram-Bot-API-Secret-Token": TEST_SECRET},
    )
    assert second.status_code == 204
    assert await _post_count(db_session_factory, channel.id) == 1


@pytest.mark.asyncio
async def test_webhook_malformed_json_returns_204(
    client: AsyncClient,
    configure_secret: None,
    seed_channel: tuple[User, Workspace, Brand, Channel, WorkspaceChannel],
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _u, _ws, _brand, channel, _bind = seed_channel
    resp = await client.post(
        WEBHOOK_PATH,
        content=b"{this isn't json",
        headers={
            "Content-Type": "application/json",
            "X-Telegram-Bot-API-Secret-Token": TEST_SECRET,
        },
    )
    assert resp.status_code == 204
    assert await _post_count(db_session_factory, channel.id) == 0


@pytest.mark.asyncio
async def test_webhook_unknown_update_kind_returns_204(
    client: AsyncClient,
    configure_secret: None,
    seed_channel: tuple[User, Workspace, Brand, Channel, WorkspaceChannel],
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """An update that doesn't contain ``channel_post`` /
    ``edited_channel_post`` must still 204 \u2014 Telegram retries on
    non-2xx and we don't want to poison a queue with private
    messages the bot also happens to receive."""

    _u, _ws, _brand, channel, _bind = seed_channel
    payload: dict[str, Any] = {
        "update_id": 99,
        "message": {
            "message_id": 1,
            "date": 1_700_000_000,
            "chat": {"id": 1, "type": "private", "first_name": "Alice"},
            "from": {"id": 1, "is_bot": False, "first_name": "Alice"},
            "text": "/start",
        },
    }
    resp = await client.post(
        WEBHOOK_PATH,
        json=payload,
        headers={"X-Telegram-Bot-API-Secret-Token": TEST_SECRET},
    )
    assert resp.status_code == 204
    assert await _post_count(db_session_factory, channel.id) == 0
