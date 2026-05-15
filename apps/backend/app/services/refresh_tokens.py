"""Refresh-token rotation service.

Adapted 1-to-1 from the reference project's ``services/refresh_tokens.py``
(PR-T7 pattern). Plaintext never stored — only SHA-256. Rotation marks
the old row revoked, persists a new row in the same family. Replay
(a presented token whose row is already revoked) wipes the whole
family and forces the user to re-authenticate.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.errors import (
    InvalidRefreshTokenError,
    RefreshTokenReplayedError,
    UserInactiveError,
)
from app.models.refresh_token import RefreshToken
from app.models.user import User, UserStatus

logger = structlog.get_logger(__name__)


def _now() -> datetime:
    return datetime.now(tz=UTC)


def hash_refresh_token(token: str) -> str:
    """SHA-256 hex digest of the raw token. Public so the
    /v1/auth/sign-out path can look the family up by hash without
    duplicating the helper."""

    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _refresh_ttl() -> timedelta:
    return timedelta(days=settings.refresh_token_expire_days)


@dataclass(frozen=True)
class IssuedRefresh:
    """Result of issuing or rotating a refresh token."""

    token: str
    row_id: uuid.UUID
    family_id: uuid.UUID
    expires_at: datetime


async def issue_refresh(
    session: AsyncSession,
    *,
    user: User,
    user_agent: str | None = None,
    ip: str | None = None,
) -> IssuedRefresh:
    """Mint a brand-new refresh-token family for ``user`` (sign-in path)."""

    plaintext = secrets.token_urlsafe(32)
    family_id = uuid.uuid4()
    return await _persist(
        session,
        user=user,
        plaintext=plaintext,
        family_id=family_id,
        parent=None,
        user_agent=user_agent,
        ip=ip,
    )


async def rotate_refresh(
    session: AsyncSession,
    *,
    presented_token: str,
    user_agent: str | None = None,
    ip: str | None = None,
) -> tuple[User, IssuedRefresh]:
    """Validate ``presented_token`` and rotate it.

    Raises:

    * :class:`InvalidRefreshTokenError` — unknown, expired, or user inactive.
    * :class:`RefreshTokenReplayedError` — row already revoked; the
      whole family is wiped before this raises.
    """

    presented_hash = hash_refresh_token(presented_token)
    result = await session.execute(
        select(RefreshToken).where(RefreshToken.token_hash == presented_hash)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise InvalidRefreshTokenError("refresh token not found")

    if row.revoked_at is not None:
        await _revoke_family(session, family_id=row.family_id)
        await session.commit()
        logger.warning(
            "auth.refresh_replay",
            family_id=str(row.family_id),
            user_id=str(row.user_id),
        )
        raise RefreshTokenReplayedError("refresh token replayed; family revoked")

    now = _now()
    expires_at = row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= now:
        raise InvalidRefreshTokenError("refresh token expired")

    user = await session.get(User, row.user_id)
    if user is None or user.status != UserStatus.ACTIVE:
        raise UserInactiveError("user not eligible for refresh")

    plaintext = secrets.token_urlsafe(32)
    issued = await _persist(
        session,
        user=user,
        plaintext=plaintext,
        family_id=row.family_id,
        parent=row,
        user_agent=user_agent,
        ip=ip,
    )

    row.revoked_at = now
    row.replaced_by = issued.row_id
    return user, issued


async def revoke_family(
    session: AsyncSession,
    *,
    family_id: uuid.UUID,
) -> None:
    """Public revoke-by-family entry point (sign-out path)."""

    await _revoke_family(session, family_id=family_id)


async def revoke_all_for_user(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
) -> None:
    """Revoke every still-active refresh family for ``user_id``."""

    now = _now()
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )


async def _persist(
    session: AsyncSession,
    *,
    user: User,
    plaintext: str,
    family_id: uuid.UUID,
    parent: RefreshToken | None,
    user_agent: str | None,
    ip: str | None,
) -> IssuedRefresh:
    now = _now()
    row = RefreshToken(
        user_id=user.id,
        token_hash=hash_refresh_token(plaintext),
        family_id=family_id,
        parent_id=parent.id if parent is not None else None,
        issued_at=now,
        expires_at=now + _refresh_ttl(),
        user_agent=user_agent,
        ip=ip,
    )
    session.add(row)
    await session.flush()
    return IssuedRefresh(
        token=plaintext,
        row_id=row.id,
        family_id=family_id,
        expires_at=row.expires_at,
    )


async def _revoke_family(
    session: AsyncSession,
    *,
    family_id: uuid.UUID,
) -> None:
    now = _now()
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )


__all__ = [
    "IssuedRefresh",
    "hash_refresh_token",
    "issue_refresh",
    "revoke_all_for_user",
    "revoke_family",
    "rotate_refresh",
]
