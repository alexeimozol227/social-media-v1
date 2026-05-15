"""Tests for sign-up email verification endpoints + register-side dispatch.

Covers:

* Registration dispatches one verification email automatically.
* ``POST /v1/auth/verify-email`` flips ``email_verified_at`` on
  correct code.
* Wrong code increments ``attempts`` and returns 400.
* 5th wrong attempt force-consumes the row → 400 + a fresh resend is
  required.
* ``POST /v1/auth/resend-verification`` is cooldown-gated.
* Already-verified user gets 409 on resend / verify.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.email_verification import EmailVerification
from app.models.user import User

# Reuses ``_CapturingEmailSender`` from conftest.
from tests.conftest import _CapturingEmailSender

CODE_RE = re.compile(r"\b(\d{6})\b")


def _extract_code(body: str) -> str:
    """Pull the 6-digit code out of the rendered email body."""

    match = CODE_RE.search(body)
    assert match is not None, body
    return match.group(1)


async def _register(client: AsyncClient, email: str = "alice@example.com") -> dict:
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
    return resp.json()


async def _login(client: AsyncClient, email: str) -> None:
    resp = await client.post(
        "/v1/auth/login",
        json={"email": email, "password": "S3curePass!"},
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_register_dispatches_verification_email(
    client: AsyncClient,
    email_sender: _CapturingEmailSender,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _register(client, "alice@example.com")

    assert len(email_sender.sent) == 1
    sent = email_sender.sent[0]
    assert sent["to"] == "alice@example.com"
    assert sent["purpose"] == "email_verification.signup"

    # A row exists in the verification store, not consumed.
    async with db_session_factory() as session:
        rows = (await session.execute(select(EmailVerification))).scalars().all()
        assert len(rows) == 1
        assert rows[0].purpose == "signup"
        assert rows[0].consumed_at is None
        assert rows[0].attempts == 0


@pytest.mark.asyncio
async def test_verify_email_success_marks_user_verified(
    client: AsyncClient,
    email_sender: _CapturingEmailSender,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _register(client, "alice@example.com")
    await _login(client, "alice@example.com")

    code = _extract_code(email_sender.sent[0]["body"])
    resp = await client.post("/v1/auth/verify-email", json={"code": code})
    assert resp.status_code == 204, resp.text

    async with db_session_factory() as session:
        user = (await session.execute(select(User))).scalar_one()
        assert user.email_verified_at is not None
        row = (await session.execute(select(EmailVerification))).scalar_one()
        assert row.consumed_at is not None


@pytest.mark.asyncio
async def test_verify_email_wrong_code_400_increments_attempts(
    client: AsyncClient,
    email_sender: _CapturingEmailSender,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _register(client, "alice@example.com")
    await _login(client, "alice@example.com")

    resp = await client.post("/v1/auth/verify-email", json={"code": "000000"})
    assert resp.status_code == 400
    assert resp.json()["error_code"] == "VERIFY_CODE_INVALID"

    async with db_session_factory() as session:
        row = (await session.execute(select(EmailVerification))).scalar_one()
        assert row.attempts == 1
        assert row.consumed_at is None
        user = (await session.execute(select(User))).scalar_one()
        assert user.email_verified_at is None


@pytest.mark.asyncio
async def test_verify_email_five_wrong_attempts_force_consume(
    client: AsyncClient,
    email_sender: _CapturingEmailSender,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # Real code is unknown to us; pick something that definitely
    # won't collide with the 6-digit code from the email by reading
    # the real code first and inverting one digit.
    await _register(client, "alice@example.com")
    await _login(client, "alice@example.com")

    real_code = _extract_code(email_sender.sent[0]["body"])
    # Always-wrong code derived from the real one: bump first digit.
    wrong = str((int(real_code[0]) + 1) % 10) + real_code[1:]

    for i in range(4):
        resp = await client.post("/v1/auth/verify-email", json={"code": wrong})
        assert resp.status_code == 400, f"attempt {i + 1}"
        assert resp.json()["error_code"] == "VERIFY_CODE_INVALID"

    # 5th wrong attempt force-consumes.
    resp = await client.post("/v1/auth/verify-email", json={"code": wrong})
    assert resp.status_code == 400
    assert resp.json()["error_code"] == "VERIFY_TOO_MANY_ATTEMPTS"

    async with db_session_factory() as session:
        row = (await session.execute(select(EmailVerification))).scalar_one()
        assert row.consumed_at is not None
        assert row.attempts == 5

    # A new submit (even with the correct code from a stale row) now
    # has no active row → 404.
    resp = await client.post("/v1/auth/verify-email", json={"code": real_code})
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "NO_ACTIVE_VERIFICATION"


@pytest.mark.asyncio
async def test_verify_email_expired_400(
    client: AsyncClient,
    email_sender: _CapturingEmailSender,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _register(client, "alice@example.com")
    await _login(client, "alice@example.com")

    real_code = _extract_code(email_sender.sent[0]["body"])

    # Force-expire the row.
    async with db_session_factory() as session:
        row = (await session.execute(select(EmailVerification))).scalar_one()
        row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await session.commit()

    resp = await client.post("/v1/auth/verify-email", json={"code": real_code})
    assert resp.status_code == 400
    assert resp.json()["error_code"] == "VERIFY_CODE_EXPIRED"


@pytest.mark.asyncio
async def test_verify_email_unauthenticated_401(client: AsyncClient) -> None:
    resp = await client.post("/v1/auth/verify-email", json={"code": "123456"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_resend_verification_cooldown_429(
    client: AsyncClient,
    email_sender: _CapturingEmailSender,
) -> None:
    await _register(client, "alice@example.com")
    await _login(client, "alice@example.com")

    # First resend hits the per-row cooldown because register already
    # issued one less than 60s ago.
    resp = await client.post("/v1/auth/resend-verification", json={"lang": "ru"})
    assert resp.status_code == 429
    body = resp.json()
    assert body["error_code"] == "VERIFY_RESEND_COOLDOWN"
    assert body["retry_after_seconds"] is None or body["retry_after_seconds"] > 0


@pytest.mark.asyncio
async def test_resend_verification_after_cooldown(
    client: AsyncClient,
    email_sender: _CapturingEmailSender,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _register(client, "alice@example.com")
    await _login(client, "alice@example.com")

    # Backdate the existing row past the cooldown window.
    async with db_session_factory() as session:
        row = (await session.execute(select(EmailVerification))).scalar_one()
        row.created_at = datetime.now(UTC) - timedelta(minutes=5)
        await session.commit()

    resp = await client.post("/v1/auth/resend-verification", json={"lang": "ru"})
    assert resp.status_code == 202

    # Old row consumed, a fresh active row exists.
    async with db_session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(EmailVerification).order_by(EmailVerification.created_at)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 2
        assert rows[0].consumed_at is not None
        assert rows[1].consumed_at is None

    # And we sent a second email.
    assert len(email_sender.sent) == 2


@pytest.mark.asyncio
async def test_resend_verification_after_verified_409(
    client: AsyncClient,
    email_sender: _CapturingEmailSender,
) -> None:
    await _register(client, "alice@example.com")
    await _login(client, "alice@example.com")

    code = _extract_code(email_sender.sent[0]["body"])
    resp = await client.post("/v1/auth/verify-email", json={"code": code})
    assert resp.status_code == 204

    resp = await client.post("/v1/auth/resend-verification", json={"lang": "ru"})
    assert resp.status_code == 409
    assert resp.json()["error_code"] == "EMAIL_ALREADY_VERIFIED"


@pytest.mark.asyncio
async def test_resend_verification_unauthenticated_401(client: AsyncClient) -> None:
    resp = await client.post("/v1/auth/resend-verification", json={"lang": "ru"})
    assert resp.status_code == 401
