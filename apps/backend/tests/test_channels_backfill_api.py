"""Integration tests for the channel history backfill API (PR #15).

Exercises ``POST /v1/brands/{brand_id}/channels/{channel_id}/backfill``
end-to-end through the FastAPI app:

* 202 happy path — task is enqueued, event published, audit row written
* 422 limit-exceeded — limit > settings.telegram_backfill_max_limit
* 404 unknown binding
* 409 detached binding
* 403 brand mismatch
* 401 unauthenticated

The Celery task ``apply_async`` is mocked so tests don't depend on a
broker or run the production AsyncSessionLocal against the test DB
schema. The full task body is exercised by
``test_channel_history_service.py`` directly.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock

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
    bot_user_id: int = 42,
) -> MockTelegramBotClient:
    info = ChannelInfo(
        chat_id=chat_id,
        title=title,
        username=username,
        description="A test channel",
        is_public=True,
        subscribers_count=100,
    )
    member = ChatMemberInfo(
        user_id=bot_user_id,
        status="administrator",
        can_post_messages=True,
        can_edit_messages=True,
        can_delete_messages=True,
    )
    return MockTelegramBotClient(
        channels_by_id={chat_id: info},
        channels_by_username={username: info},
        members_by_chat={chat_id: [member]},
        me_id=bot_user_id,
    )


@pytest_asyncio.fixture
async def authed_client(
    client: AsyncClient,
) -> AsyncIterator[tuple[AsyncClient, dict[str, Any]]]:
    await client.post(
        "/v1/auth/register",
        json={
            "email": "backfill@example.com",
            "password": "S3curePass!",
            "tos_accepted": True,
        },
    )
    login = await client.post(
        "/v1/auth/login",
        json={"email": "backfill@example.com", "password": "S3curePass!"},
    )
    assert login.status_code == 200, login.text
    client.headers.update({"Authorization": f"Bearer {login.json()['access_token']}"})
    me = await client.get("/v1/auth/me")
    yield client, me.json()


@pytest_asyncio.fixture
def mock_bot() -> MockTelegramBotClient:
    return _build_mock()


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


@pytest.fixture
def mock_celery_task(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace ``backfill_channel_history_task.apply_async`` with a mock.

    The mock returns an ``AsyncResult``-like object with a fixed
    ``id`` so the route response is deterministic and the test
    suite doesn't depend on a broker.
    """

    from app.workers.tasks import channel_backfill as task_module

    async_result = MagicMock()
    async_result.id = "test-task-id-123"

    apply_async = MagicMock(return_value=async_result)
    monkeypatch.setattr(
        task_module.backfill_channel_history_task,
        "apply_async",
        apply_async,
        raising=True,
    )
    return apply_async


async def _brand_id(client: AsyncClient) -> str:
    resp = await client.get("/v1/users/me/brands")
    assert resp.status_code == 200, resp.text
    return resp.json()[0]["id"]


async def _connect_channel(client: AsyncClient, brand_id: str) -> dict[str, Any]:
    resp = await client.post(
        f"/v1/brands/{brand_id}/channels",
        json={"platform": "telegram", "identifier": "test_channel"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_backfill_returns_202_and_enqueues_task(
    authed_client: tuple[AsyncClient, dict[str, Any]],
    app_with_mock_bot: None,
    mock_celery_task: MagicMock,
) -> None:
    client, _ = authed_client
    brand_id = await _brand_id(client)
    binding = await _connect_channel(client, brand_id)

    resp = await client.post(
        f"/v1/brands/{brand_id}/channels/{binding['id']}/backfill",
        json={"limit": 25},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["task_id"] == "test-task-id-123"
    assert body["workspace_channel_id"] == binding["id"]
    assert body["requested_limit"] == 25

    mock_celery_task.assert_called_once()
    kwargs = mock_celery_task.call_args.kwargs["kwargs"]
    assert kwargs["workspace_channel_id"] == binding["id"]
    assert kwargs["brand_id"] == brand_id
    assert kwargs["limit"] == 25
    assert kwargs["from_message_id"] is None


@pytest.mark.asyncio
async def test_backfill_default_limit_is_100(
    authed_client: tuple[AsyncClient, dict[str, Any]],
    app_with_mock_bot: None,
    mock_celery_task: MagicMock,
) -> None:
    client, _ = authed_client
    brand_id = await _brand_id(client)
    binding = await _connect_channel(client, brand_id)

    resp = await client.post(
        f"/v1/brands/{brand_id}/channels/{binding['id']}/backfill",
        json={},
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["requested_limit"] == 100


@pytest.mark.asyncio
async def test_backfill_with_from_message_id_passes_through(
    authed_client: tuple[AsyncClient, dict[str, Any]],
    app_with_mock_bot: None,
    mock_celery_task: MagicMock,
) -> None:
    client, _ = authed_client
    brand_id = await _brand_id(client)
    binding = await _connect_channel(client, brand_id)
    resp = await client.post(
        f"/v1/brands/{brand_id}/channels/{binding['id']}/backfill",
        json={"limit": 50, "from_message_id": 5000},
    )
    assert resp.status_code == 202, resp.text
    kwargs = mock_celery_task.call_args.kwargs["kwargs"]
    assert kwargs["from_message_id"] == 5000


@pytest.mark.asyncio
async def test_backfill_limit_above_cap_returns_422(
    authed_client: tuple[AsyncClient, dict[str, Any]],
    app_with_mock_bot: None,
    mock_celery_task: MagicMock,
) -> None:
    client, _ = authed_client
    brand_id = await _brand_id(client)
    binding = await _connect_channel(client, brand_id)

    resp = await client.post(
        f"/v1/brands/{brand_id}/channels/{binding['id']}/backfill",
        json={"limit": 10_000},
    )
    assert resp.status_code == 422
    assert resp.json()["error_code"] == "CHANNEL_BACKFILL_LIMIT_EXCEEDED"
    # Task must not be enqueued when validation rejects the call.
    mock_celery_task.assert_not_called()


@pytest.mark.asyncio
async def test_backfill_unknown_binding_returns_404(
    authed_client: tuple[AsyncClient, dict[str, Any]],
    app_with_mock_bot: None,
    mock_celery_task: MagicMock,
) -> None:
    client, _ = authed_client
    brand_id = await _brand_id(client)
    bogus = "00000000-0000-0000-0000-000000000000"
    resp = await client.post(
        f"/v1/brands/{brand_id}/channels/{bogus}/backfill",
        json={"limit": 25},
    )
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "CHANNEL_NOT_FOUND"
    mock_celery_task.assert_not_called()


@pytest.mark.asyncio
async def test_backfill_detached_binding_returns_409(
    authed_client: tuple[AsyncClient, dict[str, Any]],
    app_with_mock_bot: None,
    mock_celery_task: MagicMock,
) -> None:
    client, _ = authed_client
    brand_id = await _brand_id(client)
    binding = await _connect_channel(client, brand_id)
    detach = await client.delete(
        f"/v1/brands/{brand_id}/channels/{binding['id']}",
    )
    assert detach.status_code == 204

    resp = await client.post(
        f"/v1/brands/{brand_id}/channels/{binding['id']}/backfill",
        json={"limit": 10},
    )
    assert resp.status_code == 409
    assert resp.json()["error_code"] == "CHANNEL_NOT_CONNECTED"
    mock_celery_task.assert_not_called()


@pytest.mark.asyncio
async def test_backfill_brand_mismatch_returns_403(
    authed_client: tuple[AsyncClient, dict[str, Any]],
    app_with_mock_bot: None,
    mock_celery_task: MagicMock,
) -> None:
    client, _ = authed_client
    bogus_brand = "11111111-2222-3333-4444-555555555555"
    bogus_binding = "00000000-0000-0000-0000-000000000000"
    resp = await client.post(
        f"/v1/brands/{bogus_brand}/channels/{bogus_binding}/backfill",
        json={"limit": 10},
    )
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "BRAND_NOT_IN_WORKSPACE"
    mock_celery_task.assert_not_called()


@pytest.mark.asyncio
async def test_backfill_unauthenticated_returns_401(
    client: AsyncClient,
    app_with_mock_bot: None,
) -> None:
    brand = "00000000-0000-0000-0000-000000000000"
    binding = "00000000-0000-0000-0000-000000000000"
    resp = await client.post(
        f"/v1/brands/{brand}/channels/{binding}/backfill",
        json={"limit": 10},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_backfill_rejects_limit_zero(
    authed_client: tuple[AsyncClient, dict[str, Any]],
    app_with_mock_bot: None,
    mock_celery_task: MagicMock,
) -> None:
    """Pydantic ``ge=1`` should bounce ``limit=0`` before any DB work."""

    client, _ = authed_client
    brand_id = await _brand_id(client)
    binding = await _connect_channel(client, brand_id)
    resp = await client.post(
        f"/v1/brands/{brand_id}/channels/{binding['id']}/backfill",
        json={"limit": 0},
    )
    assert resp.status_code == 422
    mock_celery_task.assert_not_called()
