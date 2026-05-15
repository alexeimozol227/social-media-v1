"""Email verification flow (sign-up + change).

Adapted from the reference project's
``app/services/email_verifications.py`` (PR-F1). Pure-service module —
the route layer in ``app.api.routes.email_verifications`` translates
domain exceptions to HTTP and owns the per-purpose side effect
(setting ``users.email_verified_at`` for signup; swapping
``users.email`` for change — wired in a follow-up PR).

Invariant: at most one **active** row per ``(user_id, purpose)``,
where "active" means ``consumed_at IS NULL AND expires_at > now()``.
Requesting a new code marks the previous active row consumed.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.email import EmailSender
from app.core.security import pwd_context
from app.errors import (
    NoActiveVerificationError,
    VerifyCodeExpiredError,
    VerifyCodeInvalidError,
    VerifyResendCooldownError,
    VerifyTooManyAttemptsError,
)
from app.models.email_verification import (
    PURPOSE_SIGNUP,
    VALID_PURPOSES,
    EmailVerification,
)
from app.models.user import User
from app.services import email_templates

CODE_LENGTH = 6


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _as_utc(value: datetime) -> datetime:
    """Treat a naive datetime as UTC (SQLite drops tz info on read)."""

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _generate_code() -> str:
    """6-digit numeric code, drawn uniformly from 0-9.

    ``secrets.choice`` per digit avoids the modulo-bias trap of
    ``randint(0, 999_999)``.
    """

    return "".join(secrets.choice("0123456789") for _ in range(CODE_LENGTH))


def _ttl() -> timedelta:
    return timedelta(minutes=settings.email_verification_ttl_minutes)


def _resend_cooldown() -> timedelta:
    return timedelta(seconds=settings.email_verification_resend_cooldown_seconds)


def _max_attempts() -> int:
    return settings.email_verification_max_attempts


async def _get_active_verification(
    session: AsyncSession,
    user_id: object,
    purpose: str,
) -> EmailVerification | None:
    """Return the most recent non-consumed row for ``(user_id, purpose)``.

    Expiry is checked at call sites — this function does not filter
    on it so :func:`confirm_verification` can distinguish "expired"
    from "no row at all".
    """

    stmt = (
        select(EmailVerification)
        .where(
            EmailVerification.user_id == user_id,
            EmailVerification.purpose == purpose,
            EmailVerification.consumed_at.is_(None),
        )
        .order_by(EmailVerification.created_at.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def request_verification(
    session: AsyncSession,
    email_sender: EmailSender,
    user: User,
    *,
    purpose: str,
    email: str | None = None,
    lang: email_templates.Lang = "ru",
) -> EmailVerification:
    """Issue a fresh verification row + dispatch the email.

    For ``purpose='signup'`` the target email is always the user's
    current email; ``email`` is ignored. For ``purpose='change'`` the
    caller supplies the new email.

    Raises :class:`VerifyResendCooldownError` if the previous active
    row was created < ``email_verification_resend_cooldown_seconds``
    ago. The route handler attaches ``Retry-After``.
    """

    if purpose not in VALID_PURPOSES:
        raise ValueError(f"Unknown purpose: {purpose!r}")

    target_email = user.email if purpose == PURPOSE_SIGNUP else email
    if not target_email:
        raise ValueError("email is required for purpose='change'")

    now = _now()
    existing = await _get_active_verification(session, user.id, purpose)
    if existing is not None:
        age = now - _as_utc(existing.created_at)
        if age < _resend_cooldown():
            retry_after = int((_resend_cooldown() - age).total_seconds()) or 1
            raise VerifyResendCooldownError(
                retry_after_seconds=retry_after,
            )
        existing.consumed_at = now

    code = _generate_code()
    verification = EmailVerification(
        user_id=user.id,
        email=target_email.lower(),
        purpose=purpose,
        code_hash=pwd_context.hash(code),
        expires_at=now + _ttl(),
        attempts=0,
    )
    session.add(verification)
    await session.commit()
    await session.refresh(verification)

    ttl_minutes = int(_ttl().total_seconds() // 60) or 1
    if purpose == PURPOSE_SIGNUP:
        subject, body = email_templates.signup_verification(
            code=code,
            ttl_minutes=ttl_minutes,
            lang=lang,
        )
    else:
        subject, body = email_templates.change_verification(
            code=code,
            ttl_minutes=ttl_minutes,
            lang=lang,
        )
    await email_sender.send(
        to=target_email,
        subject=subject,
        body=body,
        purpose=f"email_verification.{purpose}",
    )

    return verification


async def confirm_verification(
    session: AsyncSession,
    user: User,
    *,
    purpose: str,
    code: str,
) -> EmailVerification:
    """Validate ``code`` against the active row for ``(user, purpose)``.

    On success, sets ``consumed_at`` and returns the row. The route
    handler is responsible for the side effect — for ``signup``,
    flipping ``users.email_verified_at``.

    Raises:

    * :class:`NoActiveVerificationError` — no row.
    * :class:`VerifyCodeExpiredError` — row past ``expires_at``.
    * :class:`VerifyTooManyAttemptsError` — 5th wrong attempt; row
      force-consumed.
    * :class:`VerifyCodeInvalidError` — wrong code, retries remain.
    """

    if purpose not in VALID_PURPOSES:
        raise ValueError(f"Unknown purpose: {purpose!r}")

    verification = await _get_active_verification(session, user.id, purpose)
    if verification is None:
        raise NoActiveVerificationError()

    now = _now()
    if _as_utc(verification.expires_at) <= now:
        verification.consumed_at = now
        await session.commit()
        raise VerifyCodeExpiredError()

    if not pwd_context.verify(code, verification.code_hash):
        verification.attempts += 1
        if verification.attempts >= _max_attempts():
            verification.consumed_at = now
            await session.commit()
            raise VerifyTooManyAttemptsError()
        await session.commit()
        raise VerifyCodeInvalidError()

    verification.consumed_at = now
    await session.commit()
    await session.refresh(verification)
    return verification


__all__ = [
    "CODE_LENGTH",
    "confirm_verification",
    "request_verification",
]
