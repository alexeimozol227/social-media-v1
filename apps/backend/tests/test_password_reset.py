"""Tests for password-reset flow.

Covers:

* ``POST /v1/auth/forgot-password`` always returns 202 — including
  for unknown emails (no enumeration leak).
* A row is committed for known emails; an email is dispatched.
* ``POST /v1/auth/reset-password`` with a valid token sets the new
  password, bumps ``users.token_version``, revokes refresh families,
  marks the reset row consumed.
* After reset, the OLD access token returns 401 (token_version bumped)
  and the OLD refresh token returns 401 (family revoked).
* Login with the new password works; the old one no longer does.
* Invalid / expired / replayed tokens return 400 with the right error
  code.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from urllib.parse import unquote, urlparse

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.password_reset import PasswordReset
from app.models.refresh_token import RefreshToken
from app.models.user import User
from tests.conftest import _CapturingEmailSender

URL_RE = re.compile(r"https?://[^\s]+")


def _extract_reset_token(body: str) -> str:
    """Pull the ``?token=...`` value out of the reset email body."""

    match = URL_RE.search(body)
    assert match is not None, body
    parsed = urlparse(match.group(0))
    qs = dict(p.split("=", 1) for p in parsed.query.split("&") if "=" in p)
    return unquote(qs["token"])


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


@pytest.mark.asyncio
async def test_forgot_password_unknown_email_returns_202(
    client: AsyncClient,
    email_sender: _CapturingEmailSender,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    resp = await client.post(
        "/v1/auth/forgot-password",
        json={"email": "nobody@example.com", "lang": "ru"},
    )
    assert resp.status_code == 202

    # No DB row, no email dispatched. The verification email from
    # the very first register call is the only one possible; here we
    # never registered, so it must be empty.
    async with db_session_factory() as session:
        rows = (await session.execute(select(PasswordReset))).scalars().all()
        assert rows == []
    # Subject filter just in case other fixtures evolve.
    assert not any(e for e in email_sender.sent if e["purpose"] == "password_reset")


@pytest.mark.asyncio
async def test_forgot_password_known_email_dispatches(
    client: AsyncClient,
    email_sender: _CapturingEmailSender,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _register(client, "alice@example.com")
    email_sender.sent.clear()

    resp = await client.post(
        "/v1/auth/forgot-password",
        json={"email": "alice@example.com", "lang": "ru"},
    )
    assert resp.status_code == 202

    async with db_session_factory() as session:
        rows = (await session.execute(select(PasswordReset))).scalars().all()
        assert len(rows) == 1
        assert rows[0].consumed_at is None

    purposes = [e["purpose"] for e in email_sender.sent]
    assert "password_reset" in purposes


@pytest.mark.asyncio
async def test_reset_password_invalid_token_400(client: AsyncClient) -> None:
    resp = await client.post(
        "/v1/auth/reset-password",
        json={
            "token": "obviously-not-a-real-token-zzz",
            "new_password": "BrandNewPass!",
            "lang": "ru",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error_code"] == "PASSWORD_RESET_INVALID"


@pytest.mark.asyncio
async def test_reset_password_expired_400(
    client: AsyncClient,
    email_sender: _CapturingEmailSender,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _register(client, "alice@example.com")
    email_sender.sent.clear()
    await client.post(
        "/v1/auth/forgot-password",
        json={"email": "alice@example.com", "lang": "ru"},
    )
    reset_emails = [e for e in email_sender.sent if e["purpose"] == "password_reset"]
    assert reset_emails
    token = _extract_reset_token(reset_emails[0]["body"])

    # Force-expire.
    async with db_session_factory() as session:
        row = (await session.execute(select(PasswordReset))).scalar_one()
        row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await session.commit()

    resp = await client.post(
        "/v1/auth/reset-password",
        json={
            "token": token,
            "new_password": "BrandNewPass!",
            "lang": "ru",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error_code"] == "PASSWORD_RESET_EXPIRED"


@pytest.mark.asyncio
async def test_reset_password_consumed_400_on_replay(
    client: AsyncClient,
    email_sender: _CapturingEmailSender,
) -> None:
    await _register(client, "alice@example.com")
    email_sender.sent.clear()
    await client.post(
        "/v1/auth/forgot-password",
        json={"email": "alice@example.com", "lang": "ru"},
    )
    reset_emails = [e for e in email_sender.sent if e["purpose"] == "password_reset"]
    token = _extract_reset_token(reset_emails[0]["body"])

    # First use → 204.
    resp = await client.post(
        "/v1/auth/reset-password",
        json={
            "token": token,
            "new_password": "BrandNewPass!",
            "lang": "ru",
        },
    )
    assert resp.status_code == 204, resp.text

    # Replay → 400 + PASSWORD_RESET_CONSUMED.
    resp = await client.post(
        "/v1/auth/reset-password",
        json={
            "token": token,
            "new_password": "EvenNewerPass!",
            "lang": "ru",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error_code"] == "PASSWORD_RESET_CONSUMED"


@pytest.mark.asyncio
async def test_reset_password_success_revokes_sessions_and_updates_password(
    client: AsyncClient,
    email_sender: _CapturingEmailSender,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _register(client, "alice@example.com")
    # Sign in once to get a refresh family + access cookie.
    assert await _login(client, "alice@example.com", "S3curePass!") == 200

    # Snapshot pre-reset state.
    async with db_session_factory() as session:
        user_pre = (await session.execute(select(User))).scalar_one()
        tv_pre = user_pre.token_version
        active_pre = (
            (await session.execute(select(RefreshToken).where(RefreshToken.revoked_at.is_(None))))
            .scalars()
            .all()
        )
        assert len(active_pre) >= 1

    email_sender.sent.clear()
    await client.post(
        "/v1/auth/forgot-password",
        json={"email": "alice@example.com", "lang": "ru"},
    )
    reset_emails = [e for e in email_sender.sent if e["purpose"] == "password_reset"]
    token = _extract_reset_token(reset_emails[0]["body"])

    resp = await client.post(
        "/v1/auth/reset-password",
        json={
            "token": token,
            "new_password": "BrandNewPass!",
            "lang": "ru",
        },
    )
    assert resp.status_code == 204, resp.text

    # 1) token_version bumped.
    # 2) every refresh family revoked.
    # 3) reset row consumed.
    # 4) Courtesy email dispatched.
    async with db_session_factory() as session:
        user_post = (await session.execute(select(User))).scalar_one()
        assert user_post.token_version == tv_pre + 1
        active_post = (
            (await session.execute(select(RefreshToken).where(RefreshToken.revoked_at.is_(None))))
            .scalars()
            .all()
        )
        assert active_post == []
        row = (await session.execute(select(PasswordReset))).scalar_one()
        assert row.consumed_at is not None

    purposes = [e["purpose"] for e in email_sender.sent]
    assert "password_reset_done" in purposes

    # 5) Old password no longer works; new one does.
    assert await _login(client, "alice@example.com", "S3curePass!") == 401
    assert await _login(client, "alice@example.com", "BrandNewPass!") == 200


@pytest.mark.asyncio
async def test_reset_password_revokes_old_access_token(
    client: AsyncClient,
    email_sender: _CapturingEmailSender,
) -> None:
    """The old JWT must be rejected after the token_version bump,
    even though it hasn't expired yet."""

    await _register(client, "alice@example.com")
    assert await _login(client, "alice@example.com", "S3curePass!") == 200

    # Grab the live access cookie (proves we're authenticated).
    resp = await client.get("/v1/auth/me")
    assert resp.status_code == 200

    email_sender.sent.clear()
    await client.post(
        "/v1/auth/forgot-password",
        json={"email": "alice@example.com", "lang": "ru"},
    )
    reset_emails = [e for e in email_sender.sent if e["purpose"] == "password_reset"]
    token = _extract_reset_token(reset_emails[0]["body"])

    # Stash the access cookie value BEFORE reset (the reset response
    # clears it for the current client). We want to prove the
    # value-based check rejects the stale token even when sent
    # explicitly.
    access_cookie = client.cookies.get("sm_access")
    assert access_cookie

    resp = await client.post(
        "/v1/auth/reset-password",
        json={
            "token": token,
            "new_password": "BrandNewPass!",
            "lang": "ru",
        },
    )
    assert resp.status_code == 204

    # Force-replay the stale access token.
    resp = await client.get(
        "/v1/auth/me",
        headers={"Authorization": f"Bearer {access_cookie}"},
    )
    assert resp.status_code == 401
