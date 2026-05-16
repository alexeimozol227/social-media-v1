"""Integration tests for the channels HTTP API (PR #14).

Drives the FastAPI app end-to-end:

* register + login to get an access token
* override the Telegram Bot adapter with
  :class:`MockTelegramBotClient`
* exercise POST/GET/DELETE/POST verify endpoints
* assert the active-brand resolution path (JWT claim + header
  override) + the 4xx error mapping
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.adapters.social import (
    ChannelInfo,
    ChatMemberInfo,
    MockTelegramBotClient,
    TelegramBotClient,
)
from app.api.routes.channels import _bot_client


def _build_mock(
    *,
    chat_id: int = -1001234567890,
    username: str = "test_channel",
    title: str = "Test Channel",
    admin_can_post: bool = True,
    bot_user_id: int = 42,
) -> MockTelegramBotClient:
    info = ChannelInfo(
        chat_id=chat_id,
        title=title,
        username=username,
        description="Test description",
        is_public=bool(username),
        subscribers_count=100,
    )
    member = ChatMemberInfo(
        user_id=bot_user_id,
        status="administrator",
        can_post_messages=admin_can_post,
        can_edit_messages=admin_can_post,
        can_delete_messages=admin_can_post,
    )
    return MockTelegramBotClient(
        channels_by_id={chat_id: info},
        channels_by_username={username: info} if username else {},
        members_by_chat={chat_id: [member]},
        me_id=bot_user_id,
    )


@pytest_asyncio.fixture
async def authed_client(
    client: AsyncClient,
) -> AsyncIterator[tuple[AsyncClient, str, dict[str, Any]]]:
    """Register + log in, return ``(client, access_token, me_body)``."""

    await client.post(
        "/v1/auth/register",
        json={
            "email": "owner@example.com",
            "password": "S3curePass!",
            "tos_accepted": True,
        },
    )
    login = await client.post(
        "/v1/auth/login",
        json={"email": "owner@example.com", "password": "S3curePass!"},
    )
    assert login.status_code == 200, login.text
    access = login.json()["access_token"]
    client.headers.update({"Authorization": f"Bearer {access}"})
    me = await client.get("/v1/auth/me")
    assert me.status_code == 200, me.text
    yield client, access, me.json()


@pytest_asyncio.fixture
def mock_bot() -> MockTelegramBotClient:
    return _build_mock()


@pytest_asyncio.fixture
async def app_with_mock_bot(
    mock_bot: MockTelegramBotClient,
) -> AsyncIterator[None]:
    """Override ``_bot_client`` dependency so the API uses the mock."""

    from app.main import app

    def _provide_bot() -> TelegramBotClient:
        return mock_bot

    app.dependency_overrides[_bot_client] = _provide_bot
    try:
        yield
    finally:
        app.dependency_overrides.pop(_bot_client, None)


async def _brand_id(client: AsyncClient) -> str:
    """Resolve the user's default brand id via the brand-switcher endpoint."""

    resp = await client.get("/v1/users/me/brands")
    assert resp.status_code == 200, resp.text
    brands = resp.json()
    assert len(brands) >= 1
    return brands[0]["id"]


@pytest.mark.asyncio
async def test_list_my_brands_returns_default(
    authed_client: tuple[AsyncClient, str, dict[str, Any]],
) -> None:
    client, _, _ = authed_client
    resp = await client.get("/v1/users/me/brands")
    assert resp.status_code == 200, resp.text
    brands = resp.json()
    assert len(brands) == 1
    assert brands[0]["is_default"] is True
    assert brands[0]["name"]
    assert brands[0]["content_language"] == "ru"


@pytest.mark.asyncio
async def test_connect_channel_returns_201_and_persists(
    authed_client: tuple[AsyncClient, str, dict[str, Any]],
    app_with_mock_bot: None,
) -> None:
    client, _, _ = authed_client
    brand_id = await _brand_id(client)

    resp = await client.post(
        f"/v1/brands/{brand_id}/channels",
        json={"platform": "telegram", "identifier": "test_channel"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["platform"] == "telegram"
    assert body["username"] == "test_channel"
    assert body["title"] == "Test Channel"
    assert body["role"] == "owned"
    assert body["disconnected_at"] is None
    assert body["bot_admin_rights"]["can_post_messages"] is True

    # GET returns the same row.
    listing = await client.get(f"/v1/brands/{brand_id}/channels")
    assert listing.status_code == 200, listing.text
    payload = listing.json()
    assert payload["total"] == 1
    assert payload["items"][0]["id"] == body["id"]


@pytest.mark.asyncio
async def test_connect_strips_leading_at_sign(
    authed_client: tuple[AsyncClient, str, dict[str, Any]],
    app_with_mock_bot: None,
) -> None:
    client, _, _ = authed_client
    brand_id = await _brand_id(client)
    resp = await client.post(
        f"/v1/brands/{brand_id}/channels",
        json={"identifier": "@test_channel"},
    )
    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_connect_unknown_channel_returns_404(
    authed_client: tuple[AsyncClient, str, dict[str, Any]],
    app_with_mock_bot: None,
) -> None:
    client, _, _ = authed_client
    brand_id = await _brand_id(client)
    resp = await client.post(
        f"/v1/brands/{brand_id}/channels",
        json={"identifier": "no_such_channel"},
    )
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "CHANNEL_NOT_FOUND"


@pytest.mark.asyncio
async def test_connect_bot_not_admin_returns_403(
    authed_client: tuple[AsyncClient, str, dict[str, Any]],
    mock_bot: MockTelegramBotClient,
    app_with_mock_bot: None,
) -> None:
    client, _, _ = authed_client
    brand_id = await _brand_id(client)
    # Clear admins → bot is "left".
    mock_bot.members_by_chat[-1001234567890] = []
    resp = await client.post(
        f"/v1/brands/{brand_id}/channels",
        json={"identifier": "test_channel"},
    )
    assert resp.status_code == 409
    assert resp.json()["error_code"] == "BOT_NOT_ADMIN"


@pytest.mark.asyncio
async def test_connect_admin_without_post_permission_returns_409(
    authed_client: tuple[AsyncClient, str, dict[str, Any]],
    mock_bot: MockTelegramBotClient,
    app_with_mock_bot: None,
) -> None:
    client, _, _ = authed_client
    brand_id = await _brand_id(client)
    mock_bot.members_by_chat[-1001234567890] = [
        ChatMemberInfo(
            user_id=42,
            status="administrator",
            can_post_messages=False,
        )
    ]
    resp = await client.post(
        f"/v1/brands/{brand_id}/channels",
        json={"identifier": "test_channel"},
    )
    assert resp.status_code == 409
    assert resp.json()["error_code"] == "BOT_MISSING_POST_PERMISSION"


@pytest.mark.asyncio
async def test_duplicate_connect_returns_409(
    authed_client: tuple[AsyncClient, str, dict[str, Any]],
    app_with_mock_bot: None,
) -> None:
    client, _, _ = authed_client
    brand_id = await _brand_id(client)
    first = await client.post(
        f"/v1/brands/{brand_id}/channels",
        json={"identifier": "test_channel"},
    )
    assert first.status_code == 201, first.text
    dup = await client.post(
        f"/v1/brands/{brand_id}/channels",
        json={"identifier": "test_channel"},
    )
    assert dup.status_code == 409
    assert dup.json()["error_code"] == "CHANNEL_ALREADY_CONNECTED"


@pytest.mark.asyncio
async def test_detach_channel_marks_disconnected(
    authed_client: tuple[AsyncClient, str, dict[str, Any]],
    app_with_mock_bot: None,
) -> None:
    client, _, _ = authed_client
    brand_id = await _brand_id(client)
    create = await client.post(
        f"/v1/brands/{brand_id}/channels",
        json={"identifier": "test_channel"},
    )
    binding_id = create.json()["id"]

    detach = await client.delete(
        f"/v1/brands/{brand_id}/channels/{binding_id}",
    )
    assert detach.status_code == 204

    listing = await client.get(f"/v1/brands/{brand_id}/channels")
    assert listing.json()["total"] == 0

    listing_all = await client.get(
        f"/v1/brands/{brand_id}/channels?include_disconnected=true",
    )
    assert listing_all.json()["total"] == 1
    assert listing_all.json()["items"][0]["disconnected_at"] is not None


@pytest.mark.asyncio
async def test_detach_unknown_channel_returns_404(
    authed_client: tuple[AsyncClient, str, dict[str, Any]],
    app_with_mock_bot: None,
) -> None:
    client, _, _ = authed_client
    brand_id = await _brand_id(client)
    # well-formed UUID that doesn't exist
    missing = "00000000-0000-0000-0000-000000000000"
    resp = await client.delete(f"/v1/brands/{brand_id}/channels/{missing}")
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "CHANNEL_NOT_FOUND"


@pytest.mark.asyncio
async def test_detach_already_detached_returns_409(
    authed_client: tuple[AsyncClient, str, dict[str, Any]],
    app_with_mock_bot: None,
) -> None:
    client, _, _ = authed_client
    brand_id = await _brand_id(client)
    create = await client.post(
        f"/v1/brands/{brand_id}/channels",
        json={"identifier": "test_channel"},
    )
    binding_id = create.json()["id"]
    first = await client.delete(f"/v1/brands/{brand_id}/channels/{binding_id}")
    assert first.status_code == 204
    second = await client.delete(f"/v1/brands/{brand_id}/channels/{binding_id}")
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_verify_channel_refreshes_snapshot(
    authed_client: tuple[AsyncClient, str, dict[str, Any]],
    mock_bot: MockTelegramBotClient,
    app_with_mock_bot: None,
) -> None:
    client, _, _ = authed_client
    brand_id = await _brand_id(client)
    create = await client.post(
        f"/v1/brands/{brand_id}/channels",
        json={"identifier": "test_channel"},
    )
    assert create.status_code == 201, create.text
    binding_id = create.json()["id"]

    # Re-verify with the same admin → 200.
    ok = await client.post(
        f"/v1/brands/{brand_id}/channels/{binding_id}/verify",
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["bot_admin_rights"]["can_post_messages"] is True

    # Strip post permission → verify now fails with 409.
    mock_bot.members_by_chat[-1001234567890] = [
        ChatMemberInfo(user_id=42, status="administrator", can_post_messages=False)
    ]
    fail = await client.post(
        f"/v1/brands/{brand_id}/channels/{binding_id}/verify",
    )
    assert fail.status_code == 409
    assert fail.json()["error_code"] == "BOT_MISSING_POST_PERMISSION"


@pytest.mark.asyncio
async def test_connect_with_wrong_brand_id_in_path_returns_403(
    authed_client: tuple[AsyncClient, str, dict[str, Any]],
    app_with_mock_bot: None,
) -> None:
    client, _, _ = authed_client
    # A well-formed UUID that doesn't belong to this workspace.
    wrong_brand = "11111111-2222-3333-4444-555555555555"
    resp = await client.post(
        f"/v1/brands/{wrong_brand}/channels",
        json={"identifier": "test_channel"},
    )
    # Falls through to BRAND_NOT_IN_WORKSPACE since the active
    # brand resolved from the JWT doesn't match the path id.
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "BRAND_NOT_IN_WORKSPACE"


@pytest.mark.asyncio
async def test_unauthenticated_request_returns_401(
    client: AsyncClient,
    app_with_mock_bot: None,
) -> None:
    resp = await client.get("/v1/users/me/brands")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_active_brand_id_header_must_match_path(
    authed_client: tuple[AsyncClient, str, dict[str, Any]],
    app_with_mock_bot: None,
) -> None:
    """Header override takes effect: a bogus header → 403."""

    client, _, _ = authed_client
    brand_id = await _brand_id(client)
    bogus = "99999999-9999-9999-9999-999999999999"
    resp = await client.get(
        f"/v1/brands/{brand_id}/channels",
        headers={"X-Active-Brand-Id": bogus},
    )
    # The header points at a brand the user can't act on, so
    # ``get_active_brand`` returns a 403 before the route handler
    # runs.
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "BRAND_NOT_IN_WORKSPACE"
