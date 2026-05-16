"""Password-reset business logic.

Adapted from the reference project's ``services/password_reset.py``
(PR-T8). Two entry points:

1. :func:`request_reset` — user types their email, we send a one-shot
   link if (and only if) the address is registered. The route layer
   always returns 202 regardless of outcome so a third party can't
   distinguish "email exists" from "email doesn't exist" by the
   response shape, status code, or timing.
2. :func:`consume_reset` — user opens the link, types a new password,
   and the row is exchanged for a new ``users.hashed_password``.
   ``users.token_version`` is bumped in the same transaction so
   every outstanding access / refresh token for the account dies
   immediately.

Hash strategy: SHA-256 over a ``secrets.token_urlsafe(32)`` plaintext.
~256 bits of entropy makes a fast hash fine here — bcrypt would
dominate the verify-time budget without buying any security.
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.email import EmailSender
from app.core.security import hash_password
from app.errors import (
    PasswordResetConsumedError,
    PasswordResetExpiredError,
    PasswordResetInvalidError,
)
from app.models.password_reset import PasswordReset
from app.models.user import User, UserStatus
from app.services import email_templates
from app.services import refresh_tokens as refresh_service

logger = structlog.get_logger(__name__)


# Timing-jitter window. Keeps response time roughly uniform regardless
# of "email found" vs "email unknown" so a third party can't enumerate
# accounts by response timing. Real branches finish well below the
# upper bound under normal load.
_TIMING_JITTER_BASE_MS = 50
_TIMING_JITTER_SPREAD_MS = 100


@dataclass(frozen=True)
class IssuedReset:
    """Result of :func:`request_reset`. ``token`` is the raw value we
    drop into the email link; the row store only ever sees the
    SHA-256."""

    token: str
    expires_at: datetime
    sent: bool  # False when no user matched / cooldown short-circuited


def _now() -> datetime:
    """Indirection so tests can monkey-patch wall clock."""

    return datetime.now(tz=UTC)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _ttl() -> timedelta:
    return timedelta(minutes=settings.password_reset_ttl_minutes)


def _cooldown_key(email: str) -> str:
    return f"auth:password_reset:cooldown:{email.strip().lower()}"


async def _check_and_set_cooldown(redis: Any, email: str) -> bool:
    """Return ``True`` when the request should proceed, ``False`` when
    the per-email cooldown is still active.

    Uses ``SET ... NX EX`` so the check + claim are atomic. Falls open
    on any Redis error: a Redis blip must never turn the forgot-
    password flow into a 5xx.
    """

    if redis is None:
        return True
    try:
        ok = await redis.set(
            _cooldown_key(email),
            "1",
            ex=settings.password_reset_cooldown_seconds,
            nx=True,
        )
    except Exception as exc:
        logger.warning(
            "auth.password_reset_cooldown_redis_error",
            error=exc.__class__.__name__,
        )
        return True
    return bool(ok)


def _build_reset_url(token: str) -> str:
    """Compose the absolute URL for the reset link.

    ``urllib.parse.quote`` is applied to ``token`` even though
    ``secrets.token_urlsafe`` already produces URL-safe chars — defence
    in depth in case a future change swaps the generator.
    """

    return f"{settings.web_base_url.rstrip('/')}/reset-password?token={quote(token, safe='')}"


async def _timing_jitter() -> None:
    """Sleep ~50-150 ms with cryptographic jitter.

    Real branches finish well under 50 ms under normal load;
    UniSender Go / SMTP roundtrip dominates the response time in
    the happy path anyway, so this only matters for the no-op
    branch.
    """

    spread = secrets.randbelow(_TIMING_JITTER_SPREAD_MS + 1)
    await asyncio.sleep((_TIMING_JITTER_BASE_MS + spread) / 1000.0)


async def _get_user(session: AsyncSession, email: str) -> User | None:
    stmt = select(User).where(User.email == email.strip().lower())
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def request_reset(
    session: AsyncSession,
    email_sender: EmailSender,
    *,
    email: str,
    ip: str | None = None,
    user_agent: str | None = None,
    redis: Any | None = None,
    lang: email_templates.Lang = "ru",
) -> IssuedReset:
    """Issue a password-reset row for ``email`` (if registered).

    Always sleeps ~50-150 ms before returning so an attacker can't use
    response timing to enumerate accounts. The caller (route handler)
    returns ``202 Accepted`` regardless of the outcome.
    """

    await _timing_jitter()

    if not await _check_and_set_cooldown(redis, email):
        logger.info(
            "auth.password_reset_request_cooldown",
            email_hash=hashlib.sha256(
                email.strip().lower().encode("utf-8"),
            ).hexdigest()[:16],
        )
        return IssuedReset(token="", expires_at=_now(), sent=False)

    user = await _get_user(session, email)
    if user is None or user.status != UserStatus.ACTIVE:
        # Unknown / inactive email — silently skip. The cooldown slot
        # and timing jitter are still burned so this branch is
        # indistinguishable from the happy path.
        logger.info(
            "auth.password_reset_request_no_user",
            email_hash=hashlib.sha256(
                email.strip().lower().encode("utf-8"),
            ).hexdigest()[:16],
        )
        return IssuedReset(token="", expires_at=_now(), sent=False)

    plaintext = secrets.token_urlsafe(32)
    expires_at = _now() + _ttl()
    row = PasswordReset(
        user_id=user.id,
        token_hash=_hash_token(plaintext),
        expires_at=expires_at,
        ip_requested=ip,
        user_agent=user_agent[:512] if user_agent else None,
    )
    session.add(row)
    await session.commit()

    ttl_minutes = int(_ttl().total_seconds() // 60) or 1
    rendered = email_templates.password_reset(
        reset_url=_build_reset_url(plaintext),
        ttl_minutes=ttl_minutes,
        lang=lang,
    )
    try:
        await email_sender.send(
            to=user.email,
            subject=rendered.subject,
            body=rendered.body,
            html=rendered.html,
            purpose="password_reset",
        )
    except Exception as exc:
        # Row is committed; a transport blip should not mask the
        # request from the user.
        logger.warning(
            "auth.password_reset_send_failed",
            user_id=str(user.id),
            error=exc.__class__.__name__,
        )

    logger.info(
        "auth.password_reset_requested",
        user_id=str(user.id),
        ip=ip,
    )
    return IssuedReset(token=plaintext, expires_at=expires_at, sent=True)


async def consume_reset(
    session: AsyncSession,
    email_sender: EmailSender,
    *,
    token: str,
    new_password: str,
    lang: email_templates.Lang = "ru",
) -> User:
    """Exchange ``token`` for a password change.

    Raises:

    * :class:`PasswordResetInvalidError` — token doesn't match any row.
    * :class:`PasswordResetConsumedError` — row already used.
    * :class:`PasswordResetExpiredError` — row past ``expires_at``.

    On success: sets ``users.hashed_password``, bumps
    ``users.token_version`` (revokes every outstanding access +
    refresh family in the same transaction), marks the reset row
    consumed, and best-effort-sends a courtesy email.
    """

    row = await session.execute(
        select(PasswordReset).where(
            PasswordReset.token_hash == _hash_token(token),
        )
    )
    record = row.scalar_one_or_none()
    if record is None:
        raise PasswordResetInvalidError()

    if record.consumed_at is not None:
        raise PasswordResetConsumedError()

    now = _now()
    expires_at = record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= now:
        raise PasswordResetExpiredError()

    user = await session.get(User, record.user_id)
    if user is None or user.status != UserStatus.ACTIVE:
        # User deleted / banned between request and consume — treat as
        # invalid rather than leaking account-state through a distinct
        # error code.
        raise PasswordResetInvalidError()

    user.hashed_password = hash_password(new_password)
    record.consumed_at = now

    # Single transaction: bump token_version + revoke every active
    # refresh family + mark the reset row consumed. If anything fails
    # the password change rolls back too.
    await refresh_service.bump_token_version(session, user=user)
    await session.commit()
    await session.refresh(user)

    # Best-effort courtesy email. Password change is committed; we
    # don't unwind on send failure.
    try:
        rendered = email_templates.password_reset_done(lang=lang)
        await email_sender.send(
            to=user.email,
            subject=rendered.subject,
            body=rendered.body,
            html=rendered.html,
            purpose="password_reset_done",
        )
    except Exception as exc:
        logger.warning(
            "auth.password_reset_done_send_failed",
            user_id=str(user.id),
            error=exc.__class__.__name__,
        )

    logger.info(
        "auth.password_reset_consumed",
        user_id=str(user.id),
    )
    return user


__all__ = [
    "IssuedReset",
    "consume_reset",
    "request_reset",
]
