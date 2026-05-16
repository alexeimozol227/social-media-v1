"""Account-management tests.

Covers the three settings-page flows introduced alongside PR #14
follow-ups:

* ``POST /v1/auth/change-password``
* ``POST /v1/auth/change-email/{request,confirm}``
* ``GET / DELETE /v1/auth/sessions`` and ``POST /v1/auth/sessions/revoke-others``

The same SQLite + fakeredis fixture stack as the rest of the suite
keeps these hermetic.
"""

from __future__ import annotations

import re

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.refresh_token import RefreshToken
from app.models.user import User
from tests.conftest import _CapturingEmailSender

CODE_RE = re.compile(r"\b(\d{6})\b")


def _extract_code(body: str) -> str:
    match = CODE_RE.search(body)
    assert match is not None, body
    return match.group(1)


async def _register(client: AsyncClient, email: str = "alice@example.com") -> None:
    resp = await client.post(
        "/v1/auth/register",
        json={
            "email": email,
            "password": "S3curePass!",
            "full_name": "Alice",
            "tos_accepted": True,
        },
    )
    assert resp.status_code == 201, resp.text


async def _login(client: AsyncClient, email: str, password: str) -> int:
    resp = await client.post(
        "/v1/auth/login",
        json={"email": email, "password": password},
    )
    return resp.status_code


# ---- change-password -------------------------------------------------------


@pytest.mark.asyncio
async def test_change_password_requires_current_password(client: AsyncClient) -> None:
    await _register(client)
    assert await _login(client, "alice@example.com", "S3curePass!") == 200

    resp = await client.post(
        "/v1/auth/change-password",
        json={
            "current_password": "WrongPass!",
            "new_password": "BrandNewPass!",
        },
    )
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "INVALID_CREDENTIALS"


@pytest.mark.asyncio
async def test_change_password_rejects_same_password(client: AsyncClient) -> None:
    await _register(client)
    assert await _login(client, "alice@example.com", "S3curePass!") == 200

    resp = await client.post(
        "/v1/auth/change-password",
        json={
            "current_password": "S3curePass!",
            "new_password": "S3curePass!",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error_code"] == "PASSWORD_SAME_AS_CURRENT"


@pytest.mark.asyncio
async def test_change_password_success_bumps_tv_and_revokes(
    client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    email_sender: _CapturingEmailSender,
) -> None:
    await _register(client)
    assert await _login(client, "alice@example.com", "S3curePass!") == 200

    async with db_session_factory() as session:
        user_pre = (await session.execute(select(User))).scalar_one()
        tv_pre = user_pre.token_version

    email_sender.sent.clear()
    resp = await client.post(
        "/v1/auth/change-password",
        json={
            "current_password": "S3curePass!",
            "new_password": "BrandNewPass!",
        },
    )
    assert resp.status_code == 204, resp.text

    async with db_session_factory() as session:
        user_post = (await session.execute(select(User))).scalar_one()
        assert user_post.token_version == tv_pre + 1
        active = (
            (await session.execute(select(RefreshToken).where(RefreshToken.revoked_at.is_(None))))
            .scalars()
            .all()
        )
        assert active == []

    # Old password no longer works; new one does.
    assert await _login(client, "alice@example.com", "S3curePass!") == 401
    assert await _login(client, "alice@example.com", "BrandNewPass!") == 200

    # Courtesy email dispatched.
    purposes = [e["purpose"] for e in email_sender.sent]
    assert "password_changed" in purposes


# ---- change-email ----------------------------------------------------------


@pytest.mark.asyncio
async def test_change_email_request_requires_current_password(
    client: AsyncClient,
) -> None:
    await _register(client)
    assert await _login(client, "alice@example.com", "S3curePass!") == 200

    resp = await client.post(
        "/v1/auth/change-email/request",
        json={
            "current_password": "WrongPass!",
            "new_email": "alice2@example.com",
        },
    )
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "INVALID_CREDENTIALS"


@pytest.mark.asyncio
async def test_change_email_request_rejects_same_email(client: AsyncClient) -> None:
    await _register(client, "alice@example.com")
    assert await _login(client, "alice@example.com", "S3curePass!") == 200

    resp = await client.post(
        "/v1/auth/change-email/request",
        json={
            "current_password": "S3curePass!",
            "new_email": "ALICE@example.com",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error_code"] == "EMAIL_SAME_AS_CURRENT"


@pytest.mark.asyncio
async def test_change_email_request_rejects_taken_email(client: AsyncClient) -> None:
    await _register(client, "alice@example.com")
    await _register(client, "bob@example.com")
    assert await _login(client, "alice@example.com", "S3curePass!") == 200

    resp = await client.post(
        "/v1/auth/change-email/request",
        json={
            "current_password": "S3curePass!",
            "new_email": "bob@example.com",
        },
    )
    assert resp.status_code == 409
    assert resp.json()["error_code"] == "EMAIL_ALREADY_EXISTS"


@pytest.mark.asyncio
async def test_change_email_full_flow(
    client: AsyncClient,
    email_sender: _CapturingEmailSender,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _register(client, "alice@example.com")
    assert await _login(client, "alice@example.com", "S3curePass!") == 200

    email_sender.sent.clear()
    resp = await client.post(
        "/v1/auth/change-email/request",
        json={
            "current_password": "S3curePass!",
            "new_email": "alice2@example.com",
        },
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["sent_to"] == "alice2@example.com"

    change_emails = [e for e in email_sender.sent if e["purpose"] == "email_verification.change"]
    assert change_emails
    assert change_emails[0]["to"] == "alice2@example.com"
    code = _extract_code(change_emails[0]["body"])

    async with db_session_factory() as session:
        user_pre = (await session.execute(select(User))).scalar_one()
        tv_pre = user_pre.token_version

    email_sender.sent.clear()
    resp = await client.post(
        "/v1/auth/change-email/confirm",
        json={"code": code},
    )
    assert resp.status_code == 204, resp.text

    async with db_session_factory() as session:
        user_post = (await session.execute(select(User))).scalar_one()
        assert user_post.email == "alice2@example.com"
        assert user_post.email_verified_at is not None
        assert user_post.token_version == tv_pre + 1
        active = (
            (await session.execute(select(RefreshToken).where(RefreshToken.revoked_at.is_(None))))
            .scalars()
            .all()
        )
        assert active == []

    # Notification to the *old* address.
    notify = [e for e in email_sender.sent if e["purpose"] == "email_changed"]
    assert notify
    assert notify[0]["to"] == "alice@example.com"


@pytest.mark.asyncio
async def test_change_email_confirm_invalid_code(client: AsyncClient) -> None:
    await _register(client, "alice@example.com")
    assert await _login(client, "alice@example.com", "S3curePass!") == 200

    resp = await client.post(
        "/v1/auth/change-email/request",
        json={
            "current_password": "S3curePass!",
            "new_email": "alice2@example.com",
        },
    )
    assert resp.status_code == 202

    resp = await client.post(
        "/v1/auth/change-email/confirm",
        json={"code": "000000"},
    )
    assert resp.status_code == 400
    assert resp.json()["error_code"] == "VERIFY_CODE_INVALID"


@pytest.mark.asyncio
async def test_change_email_confirm_no_active_request(client: AsyncClient) -> None:
    await _register(client, "alice@example.com")
    assert await _login(client, "alice@example.com", "S3curePass!") == 200

    resp = await client.post(
        "/v1/auth/change-email/confirm",
        json={"code": "123456"},
    )
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "NO_ACTIVE_VERIFICATION"


# ---- sessions --------------------------------------------------------------


@pytest.mark.asyncio
async def test_sessions_list_marks_current(client: AsyncClient) -> None:
    await _register(client)
    assert await _login(client, "alice@example.com", "S3curePass!") == 200

    resp = await client.get("/v1/auth/sessions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["is_current"] is True


@pytest.mark.asyncio
async def test_sessions_list_returns_one_per_family(client: AsyncClient) -> None:
    """A rotated refresh-token chain still projects as one session."""

    await _register(client)
    assert await _login(client, "alice@example.com", "S3curePass!") == 200

    # Rotate the refresh token once.
    resp = await client.post("/v1/auth/refresh")
    assert resp.status_code == 200

    resp = await client.get("/v1/auth/sessions")
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["is_current"] is True


@pytest.mark.asyncio
async def test_sessions_revoke_other_session() -> None:
    """Login from two clients, revoke the other one, verify it can't refresh."""

    # Two separate clients == two cookie jars == two refresh families.
    from collections.abc import AsyncIterator
    from typing import Any

    import fakeredis.aioredis
    from httpx import ASGITransport
    from httpx import AsyncClient as RawClient
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )
    from sqlalchemy.pool import StaticPool

    from app.db.base import Base
    from app.db.session import get_db

    # Build a shared engine + factory used by both clients.
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )

    async def _override_get_db() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    captured: list[dict[str, Any]] = []

    class _Sender:
        async def send(
            self,
            *,
            to: str,
            subject: str,
            body: str,
            purpose: str | None = None,
        ) -> None:
            captured.append(
                {"to": to, "subject": subject, "body": body, "purpose": purpose},
            )

    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)

    from app.api.routes.auth import get_redis as _routes_get_redis  # noqa: F401
    from app.core import redis as redis_module
    from app.core.email import get_email_sender
    from app.main import app

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_email_sender] = lambda: _Sender()
    original_getter = redis_module._redis
    redis_module._redis = fake

    try:
        async with (
            RawClient(
                transport=ASGITransport(app=app),
                base_url="http://testserver",
            ) as a,
            RawClient(
                transport=ASGITransport(app=app),
                base_url="http://testserver",
            ) as b,
        ):
            # Register on client A.
            resp = await a.post(
                "/v1/auth/register",
                json={
                    "email": "alice@example.com",
                    "password": "S3curePass!",
                    "full_name": "Alice",
                    "tos_accepted": True,
                },
            )
            assert resp.status_code == 201

            # Login from A and B → two refresh families.
            resp = await a.post(
                "/v1/auth/login",
                json={"email": "alice@example.com", "password": "S3curePass!"},
            )
            assert resp.status_code == 200
            resp = await b.post(
                "/v1/auth/login",
                json={"email": "alice@example.com", "password": "S3curePass!"},
            )
            assert resp.status_code == 200

            # A sees two sessions.
            resp = await a.get("/v1/auth/sessions")
            body = resp.json()
            assert body["total"] == 2
            other = next(s for s in body["items"] if not s["is_current"])

            # A revokes B's session.
            resp = await a.delete(f"/v1/auth/sessions/{other['id']}")
            assert resp.status_code == 204

            # B's refresh now fails.
            resp = await b.post("/v1/auth/refresh")
            assert resp.status_code in {401, 403}

            # A still has exactly one session left.
            resp = await a.get("/v1/auth/sessions")
            assert resp.json()["total"] == 1
    finally:
        app.dependency_overrides.clear()
        redis_module._redis = original_getter
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


@pytest.mark.asyncio
async def test_sessions_revoke_not_found(client: AsyncClient) -> None:
    await _register(client)
    assert await _login(client, "alice@example.com", "S3curePass!") == 200

    resp = await client.delete("/v1/auth/sessions/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "SESSION_NOT_FOUND"


@pytest.mark.asyncio
async def test_sessions_revoke_others_keeps_current(
    client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _register(client)
    assert await _login(client, "alice@example.com", "S3curePass!") == 200

    # We can't easily mint a second family with the single httpx client,
    # so this case just checks that the call is a no-op-but-200 with one
    # session and doesn't kill the current one.
    resp = await client.post("/v1/auth/sessions/revoke-others")
    assert resp.status_code == 204

    resp = await client.get("/v1/auth/sessions")
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["is_current"] is True


# ---- bot info --------------------------------------------------------------


@pytest.mark.asyncio
async def test_bot_info_returns_username(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "telegram_bot_username", "SocialMediaV1Bot")

    await _register(client)
    assert await _login(client, "alice@example.com", "S3curePass!") == 200

    resp = await client.get("/v1/integrations/telegram/bot-info")
    assert resp.status_code == 200
    body = resp.json()
    assert body["username"] == "SocialMediaV1Bot"
    assert body["deep_link"].startswith("https://t.me/SocialMediaV1Bot")


@pytest.mark.asyncio
async def test_bot_info_strips_at_prefix(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "telegram_bot_username", "@SocialMediaV1Bot")

    await _register(client)
    assert await _login(client, "alice@example.com", "S3curePass!") == 200

    resp = await client.get("/v1/integrations/telegram/bot-info")
    assert resp.status_code == 200
    assert resp.json()["username"] == "SocialMediaV1Bot"


@pytest.mark.asyncio
async def test_bot_info_503_when_not_configured(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "telegram_bot_username", "")

    await _register(client)
    assert await _login(client, "alice@example.com", "S3curePass!") == 200

    resp = await client.get("/v1/integrations/telegram/bot-info")
    assert resp.status_code == 503
    assert resp.json()["error_code"] == "TELEGRAM_BOT_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_bot_info_requires_auth(client: AsyncClient) -> None:
    resp = await client.get("/v1/integrations/telegram/bot-info")
    assert resp.status_code == 401
