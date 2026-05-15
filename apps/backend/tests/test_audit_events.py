"""Tests for the ``audit_events`` recorder + auth-flow wiring (PR #5).

Covers the new ``user.*`` event types we now emit from
``app/api/routes/auth.py`` + ``app/api/routes/email_verifications.py``
+ ``app/api/routes/password_reset.py``. Each test issues real HTTP
requests against the in-memory FastAPI app and then queries
``audit_events`` directly so we lock in the exact column values the
admin lens will read in Sprint 4.

docs/04-architecture.md §10.1 + §11 + D57.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import AuditEvent, User
from app.models.audit_event import AuditSeverity


async def _register(
    client: AsyncClient, email: str = "alice@example.com", password: str = "S3curePass!"
) -> None:
    resp = await client.post(
        "/v1/auth/register",
        json={"email": email, "password": password, "tos_accepted": True},
        headers={"User-Agent": "pytest-ua/1.0"},
    )
    assert resp.status_code == 201, resp.text


async def _all_events(
    factory: async_sessionmaker[AsyncSession],
    *,
    event_type: str | None = None,
) -> list[AuditEvent]:
    async with factory() as s:
        stmt = select(AuditEvent).order_by(AuditEvent.created_at)
        if event_type is not None:
            stmt = stmt.where(AuditEvent.event_type == event_type)
        rows = (await s.execute(stmt)).scalars().all()
    return list(rows)


async def _user_by_email(factory: async_sessionmaker[AsyncSession], email: str) -> User:
    async with factory() as s:
        row = (await s.execute(select(User).where(User.email == email.lower()))).scalar_one()
    return row


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_emits_user_registered(
    client: AsyncClient, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _register(client, "alice@example.com")
    events = await _all_events(db_session_factory, event_type="user.registered")
    assert len(events) == 1
    ev = events[0]
    assert ev.severity == AuditSeverity.INFO
    assert ev.user_id is not None
    assert ev.meta == {"email": "alice@example.com"}
    assert ev.user_agent == "pytest-ua/1.0"
    # The ASGI test transport reports no client → ``ip_address`` is None.
    # The User-Agent assertion above is the load-bearing one.


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_success_no_mfa_emits_login_success_info(
    client: AsyncClient, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _register(client, "bob@example.com")
    resp = await client.post(
        "/v1/auth/login",
        json={"email": "bob@example.com", "password": "S3curePass!"},
        headers={"User-Agent": "pytest-ua/1.0"},
    )
    assert resp.status_code == 200

    events = await _all_events(db_session_factory, event_type="user.login_success")
    assert len(events) == 1
    ev = events[0]
    assert ev.severity == AuditSeverity.INFO
    assert ev.user_id is not None
    assert ev.meta == {"mfa": False}


@pytest.mark.asyncio
async def test_login_invalid_password_emits_login_failed_warning(
    client: AsyncClient, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _register(client, "carol@example.com")
    resp = await client.post(
        "/v1/auth/login",
        json={"email": "carol@example.com", "password": "WrongPass!"},
    )
    assert resp.status_code == 401

    events = await _all_events(db_session_factory, event_type="user.login_failed")
    assert len(events) == 1
    ev = events[0]
    assert ev.severity == AuditSeverity.WARNING
    # We deliberately don't attribute the failed attempt to a user
    # (the password may be wrong because someone tried the wrong
    # email). The lowered-cased email is in ``metadata`` for the
    # admin lens to bucket on.
    assert ev.user_id is None
    assert ev.meta == {"email": "carol@example.com"}


@pytest.mark.asyncio
async def test_login_unknown_email_emits_login_failed_warning(
    client: AsyncClient, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    resp = await client.post(
        "/v1/auth/login",
        json={"email": "ghost@example.com", "password": "Whatever1!"},
    )
    assert resp.status_code == 401

    events = await _all_events(db_session_factory, event_type="user.login_failed")
    assert len(events) == 1
    assert events[0].user_id is None
    assert events[0].meta["email"] == "ghost@example.com"


@pytest.mark.asyncio
async def test_login_lockout_emits_login_locked_warning(
    client: AsyncClient, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _register(client, "dave@example.com")
    for _ in range(10):
        bad = await client.post(
            "/v1/auth/login",
            json={"email": "dave@example.com", "password": "WrongPass!"},
        )
        assert bad.status_code == 401

    # 11th attempt → lockout.
    locked = await client.post(
        "/v1/auth/login",
        json={"email": "dave@example.com", "password": "S3curePass!"},
    )
    assert locked.status_code == 423

    events = await _all_events(db_session_factory, event_type="user.login_locked")
    assert len(events) == 1
    assert events[0].severity == AuditSeverity.WARNING
    assert events[0].user_id is None
    assert events[0].meta == {"email": "dave@example.com"}

    # And 10 failures were recorded before the lockout fired.
    failures = await _all_events(db_session_factory, event_type="user.login_failed")
    assert len(failures) == 10


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_logout_emits_user_logout_info(
    client: AsyncClient, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _register(client, "ed@example.com")
    login = await client.post(
        "/v1/auth/login",
        json={"email": "ed@example.com", "password": "S3curePass!"},
    )
    assert login.status_code == 200

    out = await client.post("/v1/auth/logout")
    assert out.status_code == 204

    events = await _all_events(db_session_factory, event_type="user.logout")
    assert len(events) == 1
    assert events[0].severity == AuditSeverity.INFO
    assert events[0].user_id is not None


@pytest.mark.asyncio
async def test_logout_without_session_does_not_emit_audit(
    client: AsyncClient, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """A 204 no-op logout (no refresh cookie present) is not interesting."""

    out = await client.post("/v1/auth/logout")
    assert out.status_code == 204

    events = await _all_events(db_session_factory, event_type="user.logout")
    assert events == []


# ---------------------------------------------------------------------------
# Refresh replay (CRITICAL)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_replay_emits_critical_audit(
    client: AsyncClient, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Replay = critical. The family is revoked and the audit row
    carries ``family_id`` in ``metadata`` so the admin lens can pivot
    onto every other token in the same session."""

    await _register(client, "frank@example.com")
    login = await client.post(
        "/v1/auth/login",
        json={"email": "frank@example.com", "password": "S3curePass!"},
    )
    assert login.status_code == 200
    first_refresh = login.cookies.get("sm_refresh")
    assert first_refresh is not None

    # Rotate once — the old token is now revoked but still known.
    rot = await client.post("/v1/auth/refresh")
    assert rot.status_code == 200

    # Present the *first* token again — replay.
    replay = await client.post(
        "/v1/auth/refresh",
        cookies={"sm_refresh": first_refresh},
    )
    assert replay.status_code == 403
    assert replay.json()["error_code"] == "REFRESH_TOKEN_REPLAYED"

    events = await _all_events(db_session_factory, event_type="user.refresh_replayed")
    assert len(events) == 1
    ev = events[0]
    assert ev.severity == AuditSeverity.CRITICAL
    assert ev.user_id is not None
    assert "family_id" in ev.meta


# ---------------------------------------------------------------------------
# Email verification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_email_verified_emits_audit_info(
    client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    email_sender,  # type: ignore[no-untyped-def]
) -> None:
    await _register(client, "grace@example.com")
    login = await client.post(
        "/v1/auth/login",
        json={"email": "grace@example.com", "password": "S3curePass!"},
    )
    assert login.status_code == 200

    # Pull the code out of the captured email body.
    body = email_sender.sent[0]["body"]
    # The verification template embeds the 6-digit code as a literal
    # token in the body. Tests in test_email_verification.py read it
    # the same way.
    import re

    code_match = re.search(r"\b\d{6}\b", body)
    assert code_match is not None
    code = code_match.group(0)

    resp = await client.post("/v1/auth/verify-email", json={"code": code})
    assert resp.status_code == 204, resp.text

    events = await _all_events(db_session_factory, event_type="user.email_verified")
    assert len(events) == 1
    assert events[0].severity == AuditSeverity.INFO
    assert events[0].user_id is not None


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_password_reset_consume_emits_password_changed_warning(
    client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    email_sender,  # type: ignore[no-untyped-def]
) -> None:
    """End-to-end: forgot → consume → audit row with ``flow:
    password_reset`` metadata. The metadata must NOT contain the old
    or new password (security-sensitive)."""

    await _register(client, "henry@example.com", "OldPassword1!")

    resp = await client.post(
        "/v1/auth/forgot-password",
        json={"email": "henry@example.com"},
    )
    assert resp.status_code == 202

    # The reset email body contains the URL with the token query
    # param. Tests in test_password_reset.py extract it the same way.
    import re
    from urllib.parse import parse_qs, urlparse

    body = email_sender.sent[-1]["body"]
    url_match = re.search(r"https?://\S+/reset-password\?token=\S+", body)
    assert url_match is not None
    qs = parse_qs(urlparse(url_match.group(0).rstrip(").,").rstrip()).query)
    token = qs["token"][0]

    out = await client.post(
        "/v1/auth/reset-password",
        json={"token": token, "new_password": "NewPassword1!"},
    )
    assert out.status_code == 204

    events = await _all_events(db_session_factory, event_type="user.password_changed")
    assert len(events) == 1
    ev = events[0]
    assert ev.severity == AuditSeverity.WARNING
    assert ev.user_id is not None
    assert ev.meta == {"flow": "password_reset"}
    # Sanity: no password material leaked into the audit row.
    payload_str = str(ev.meta)
    assert "OldPassword1!" not in payload_str
    assert "NewPassword1!" not in payload_str
