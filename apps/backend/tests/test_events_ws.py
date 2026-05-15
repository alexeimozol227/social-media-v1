"""WebSocket realtime stream (PR #7).

docs/06 §5 Спринт 1: ``/v1/events/ws`` — JWT-handshake auth,
per-user Redis channel, hello frame, 25 s keepalive ping.

We use Starlette's synchronous ``TestClient`` because httpx
``AsyncClient`` doesn't speak the WebSocket handshake. The same
fakeredis client must be installed on both the FastAPI app (for the
WS subscriber side) and the publisher path — the
``client``/``ws_app`` fixture below stitches both ends.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from typing import Any

import fakeredis.aioredis
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import ACCESS_COOKIE
from app.core.event_bus import publish_for_user
from app.db.session import get_db
from app.events.schemas import UserRegisteredEvent


@pytest_asyncio.fixture
async def ws_app(
    db_session_factory: async_sessionmaker[AsyncSession],
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> AsyncIterator[Any]:
    """Wire the FastAPI app for WebSocket tests.

    Same overrides as the regular ``client`` fixture, but also pins
    the engine-bound :class:`AsyncSessionLocal` to the test SQLite —
    the WS route opens its auth DB session through
    :func:`app.db.session.AsyncSessionLocal` directly (no FastAPI
    dependency injection during the handshake), so a plain
    ``dependency_overrides[get_db]`` isn't enough.
    """

    from app.core import redis as redis_module
    from app.main import app

    async def _override_get_db() -> AsyncIterator[AsyncSession]:
        async with db_session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    original_redis = redis_module._redis
    redis_module._redis = fake_redis
    try:
        yield app
    finally:
        app.dependency_overrides.clear()
        redis_module._redis = original_redis


@pytest.fixture
def ws_client(ws_app: Any) -> Iterator[TestClient]:
    with TestClient(ws_app) as c:
        yield c


def _register_and_capture_cookie(ws_client: TestClient, email: str) -> dict[str, Any]:
    """Sign up via the public route + log in to capture the access cookie.

    The register endpoint itself doesn't set the access cookie (the
    flow returns the user; the SPA then calls ``/login`` to mint a
    session). For these WS tests we go through ``/login`` to get
    the cookie populated on the TestClient.
    """

    reg = ws_client.post(
        "/v1/auth/register",
        json={
            "email": email,
            "password": "S3curePass!",
            "full_name": "WS Tester",
            "tos_accepted": True,
        },
    )
    assert reg.status_code == 201, reg.text
    user = reg.json()
    login = ws_client.post(
        "/v1/auth/login",
        json={"email": email, "password": "S3curePass!"},
    )
    assert login.status_code == 200, login.text
    return {
        "user_id": user["id"],
        "access_token": login.json()["access_token"],
    }


def test_ws_unauthorized_without_token_is_closed_with_4401(ws_client: TestClient) -> None:
    # Starlette raises WebSocketDisconnect with the close code we sent
    # — but only when the client tries to read a frame. The server's
    # ``accept`` + ``close(code=4401)`` sequence makes the underlying
    # handshake itself succeed.
    from starlette.websockets import WebSocketDisconnect

    with (
        ws_client.websocket_connect("/v1/events/ws") as ws,
        pytest.raises(WebSocketDisconnect) as exc_info,
    ):
        ws.receive_text()
    assert exc_info.value.code == 4401


def test_ws_authorized_receives_hello_frame(ws_client: TestClient) -> None:
    _register_and_capture_cookie(ws_client, "ws-hello@example.com")
    # Access cookie was set by /login — TestClient keeps it.
    assert ws_client.cookies.get(ACCESS_COOKIE) is not None

    with ws_client.websocket_connect("/v1/events/ws") as ws:
        raw = ws.receive_text()
        frame = json.loads(raw)
        assert frame.get("type") == "hello"
        # ``ts`` is present and ISO-8601-ish (we don't pin format).
        assert isinstance(frame.get("ts"), str) and frame["ts"]


def test_ws_authorized_receives_published_event(
    ws_client: TestClient,
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """Full handshake + Redis publish → WebSocket delivery round trip.

    ``TestClient`` is synchronous; the FastAPI app + its pubsub loop
    run on a worker thread the client owns. We publish from this
    test thread via ``asyncio.run`` because the test itself doesn't
    own a running loop (it's a plain ``def`` — *not* asyncio-marked).
    """

    creds = _register_and_capture_cookie(ws_client, "ws-event@example.com")
    user_id = creds["user_id"]

    with ws_client.websocket_connect("/v1/events/ws") as ws:
        # Drop the hello frame.
        ws.receive_text()

        event = UserRegisteredEvent(
            user_id=user_id,
            workspace_id="00000000-0000-0000-0000-000000000000",
            email="ws-event@example.com",
            locale="ru-RU",
            default_workspace_id="00000000-0000-0000-0000-000000000000",
        )

        async def _publish() -> None:
            await publish_for_user(fake_redis, user_id, event)

        asyncio.run(_publish())

        # Pump the WS until we see the event frame (skipping any
        # transport-only ``hello`` / ``ping`` frames if they slip in).
        for _ in range(20):
            raw = ws.receive_text()
            frame = json.loads(raw)
            if frame.get("type") in {"hello", "ping"}:
                continue
            assert frame["event_type"] == "user.registered"
            assert frame["email"] == "ws-event@example.com"
            assert frame["user_id"] == user_id
            break
        else:
            pytest.fail("WS did not deliver user.registered event")


def test_ws_invalid_token_is_closed_with_4401(ws_client: TestClient) -> None:
    # Set a bogus token on the cookie jar and confirm the handshake
    # rejects it the same way "no cookie at all" does.
    ws_client.cookies.set(ACCESS_COOKIE, "not-a-jwt")
    from starlette.websockets import WebSocketDisconnect

    with (
        ws_client.websocket_connect("/v1/events/ws") as ws,
        pytest.raises(WebSocketDisconnect) as exc_info,
    ):
        ws.receive_text()
    assert exc_info.value.code == 4401
