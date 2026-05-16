"""Integration tests for the competitor channels HTTP API (PR #18)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.adapters.social import (
    ChannelInfo,
    MockTelegramBotClient,
    TelegramBotClient,
)
from app.api.routes.competitors import _bot_client


def _build_public_mock(
    *,
    chat_id: int = -1009999999990,
    username: str = "competitor_channel",
    title: str = "Competitor",
    is_public: bool = True,
) -> MockTelegramBotClient:
    info = ChannelInfo(
        chat_id=chat_id,
        title=title,
        username=username,
        description="A competitor",
        is_public=is_public,
        subscribers_count=2500,
    )
    return MockTelegramBotClient(
        channels_by_id={chat_id: info},
        channels_by_username={username: info} if username else {},
    )


@pytest_asyncio.fixture
async def authed_client(
    client: AsyncClient,
) -> AsyncIterator[tuple[AsyncClient, str, dict[str, Any]]]:
    await client.post(
        "/v1/auth/register",
        json={
            "email": "comp@example.com",
            "password": "S3curePass!",
            "tos_accepted": True,
        },
    )
    login = await client.post(
        "/v1/auth/login",
        json={"email": "comp@example.com", "password": "S3curePass!"},
    )
    assert login.status_code == 200, login.text
    access = login.json()["access_token"]
    client.headers.update({"Authorization": f"Bearer {access}"})
    me = await client.get("/v1/auth/me")
    yield client, access, me.json()


@pytest_asyncio.fixture
def mock_bot() -> MockTelegramBotClient:
    return _build_public_mock()


@pytest_asyncio.fixture
async def app_with_mock_bot(
    mock_bot: MockTelegramBotClient,
) -> AsyncIterator[None]:
    from app.main import app

    def _provide_bot() -> TelegramBotClient:
        return mock_bot

    app.dependency_overrides[_bot_client] = _provide_bot
    try:
        yield
    finally:
        app.dependency_overrides.pop(_bot_client, None)


async def _brand_id(client: AsyncClient) -> str:
    resp = await client.get("/v1/users/me/brands")
    assert resp.status_code == 200, resp.text
    return resp.json()[0]["id"]


@pytest.mark.asyncio
async def test_connect_competitor_returns_201(
    authed_client: tuple[AsyncClient, str, dict[str, Any]],
    app_with_mock_bot: None,
) -> None:
    client, _, _ = authed_client
    brand_id = await _brand_id(client)
    resp = await client.post(
        f"/v1/brands/{brand_id}/competitors",
        json={"platform": "telegram", "identifier": "@competitor_channel"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["role"] == "competitor"
    assert body["username"] == "competitor_channel"
    assert body["disconnected_at"] is None


@pytest.mark.asyncio
async def test_list_competitors_returns_only_competitor_role(
    authed_client: tuple[AsyncClient, str, dict[str, Any]],
    app_with_mock_bot: None,
) -> None:
    client, _, _ = authed_client
    brand_id = await _brand_id(client)
    await client.post(
        f"/v1/brands/{brand_id}/competitors",
        json={"identifier": "@competitor_channel"},
    )
    resp = await client.get(f"/v1/brands/{brand_id}/competitors")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["role"] == "competitor"


@pytest.mark.asyncio
async def test_detach_competitor_returns_204_and_soft_detaches(
    authed_client: tuple[AsyncClient, str, dict[str, Any]],
    app_with_mock_bot: None,
) -> None:
    client, _, _ = authed_client
    brand_id = await _brand_id(client)
    created = await client.post(
        f"/v1/brands/{brand_id}/competitors",
        json={"identifier": "@competitor_channel"},
    )
    binding_id = created.json()["id"]

    detach = await client.delete(
        f"/v1/brands/{brand_id}/competitors/{binding_id}",
    )
    assert detach.status_code == 204

    listing = await client.get(f"/v1/brands/{brand_id}/competitors")
    assert listing.json()["total"] == 0

    full = await client.get(
        f"/v1/brands/{brand_id}/competitors?include_disconnected=true",
    )
    assert full.json()["total"] == 1
    assert full.json()["items"][0]["disconnected_at"] is not None


@pytest.mark.asyncio
async def test_duplicate_competitor_connect_returns_409(
    authed_client: tuple[AsyncClient, str, dict[str, Any]],
    app_with_mock_bot: None,
) -> None:
    client, _, _ = authed_client
    brand_id = await _brand_id(client)
    first = await client.post(
        f"/v1/brands/{brand_id}/competitors",
        json={"identifier": "@competitor_channel"},
    )
    assert first.status_code == 201, first.text
    dup = await client.post(
        f"/v1/brands/{brand_id}/competitors",
        json={"identifier": "@competitor_channel"},
    )
    assert dup.status_code == 409
    assert dup.json()["error_code"] == "COMPETITOR_ALREADY_CONNECTED"


@pytest.mark.asyncio
async def test_connect_private_channel_returns_409(
    authed_client: tuple[AsyncClient, str, dict[str, Any]],
) -> None:
    from app.main import app

    private = ChannelInfo(
        chat_id=-1008888888880,
        title="Private",
        username=None,
        description=None,
        is_public=False,
    )
    private_bot = MockTelegramBotClient(
        channels_by_id={private.chat_id: private},
        channels_by_username={},
    )

    def _provide_bot() -> TelegramBotClient:
        return private_bot

    app.dependency_overrides[_bot_client] = _provide_bot
    try:
        client, _, _ = authed_client
        brand_id = await _brand_id(client)
        resp = await client.post(
            f"/v1/brands/{brand_id}/competitors",
            json={"identifier": private.chat_id},
        )
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "COMPETITOR_NOT_PUBLIC"
    finally:
        app.dependency_overrides.pop(_bot_client, None)


@pytest.mark.asyncio
async def test_competitor_routes_brand_mismatch_returns_403(
    authed_client: tuple[AsyncClient, str, dict[str, Any]],
    app_with_mock_bot: None,
) -> None:
    client, _, _ = authed_client
    bogus = "11111111-2222-3333-4444-555555555555"
    resp = await client.post(
        f"/v1/brands/{bogus}/competitors",
        json={"identifier": "@competitor_channel"},
    )
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "BRAND_NOT_IN_WORKSPACE"


@pytest.mark.asyncio
async def test_detach_unknown_binding_returns_404(
    authed_client: tuple[AsyncClient, str, dict[str, Any]],
    app_with_mock_bot: None,
) -> None:
    client, _, _ = authed_client
    brand_id = await _brand_id(client)
    missing = "00000000-0000-0000-0000-000000000000"
    resp = await client.delete(f"/v1/brands/{brand_id}/competitors/{missing}")
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "CHANNEL_NOT_FOUND"
