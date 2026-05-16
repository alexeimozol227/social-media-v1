"""Active-sessions read + revoke helpers.

Backs ``GET /v1/auth/sessions`` (list), ``DELETE /v1/auth/sessions/{id}``
(revoke one), and ``POST /v1/auth/sessions/revoke-others`` (revoke
everything except the calling family). Together they let the user
audit and curb their active sign-ins from the Account → Security UI.

The data lives in :class:`app.models.refresh_token.RefreshToken` —
one "session" in the UI sense is the most recent (un-revoked)
row in a refresh family. We project per-family so a long-lived
session that has been rotated 100 times still shows up as a single
row, not 100.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import SessionNotFoundError
from app.models.refresh_token import RefreshToken
from app.services import refresh_tokens as refresh_service


@dataclass(frozen=True, slots=True)
class SessionRow:
    """One projected session — the head of a refresh family.

    ``id`` is the family id, NOT the row id. The SPA's "revoke this
    session" button posts the family id back so server-side rotation
    of the head row mid-flight doesn't 404 the call.
    """

    id: uuid.UUID
    user_agent: str | None
    ip: str | None
    issued_at: datetime
    expires_at: datetime
    is_current: bool


def _as_utc(value: datetime) -> datetime:
    """SQLite drops tzinfo on read; coerce naive timestamps to UTC."""

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _now() -> datetime:
    return datetime.now(tz=UTC)


async def _family_head_for_user(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
) -> dict[uuid.UUID, RefreshToken]:
    """Return the most recent (un-revoked, un-expired) row per family.

    A family rotates one row at a time — older rows in the same
    family carry ``revoked_at != None`` and ``replaced_by`` pointing
    at the live head. Picking the live head per ``family_id`` gives
    us exactly one row per logical session.
    """

    stmt = (
        select(RefreshToken)
        .where(RefreshToken.user_id == user_id)
        .order_by(RefreshToken.issued_at.desc())
    )
    result = await session.execute(stmt)
    rows = list(result.scalars().all())

    heads: dict[uuid.UUID, RefreshToken] = {}
    now = _now()
    for row in rows:
        if row.family_id in heads:
            continue
        if row.revoked_at is not None:
            continue
        if _as_utc(row.expires_at) <= now:
            continue
        heads[row.family_id] = row
    return heads


async def list_sessions_for_user(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    current_token_hash: str | None,
) -> list[SessionRow]:
    """Project every active refresh family for ``user_id``.

    ``current_token_hash`` is the SHA-256 of the refresh cookie
    carried by *this* request (set by the route handler) — used to
    flag exactly one row as ``is_current=true``. ``None`` is fine for
    callers that don't have a live refresh cookie (e.g. API
    clients on access-token-only auth): no row will be flagged
    current, which the SPA renders as "another device".
    """

    heads = await _family_head_for_user(session, user_id=user_id)
    rows: list[SessionRow] = []
    for row in heads.values():
        is_current = current_token_hash is not None and row.token_hash == current_token_hash
        rows.append(
            SessionRow(
                id=row.family_id,
                user_agent=row.user_agent,
                ip=row.ip,
                issued_at=_as_utc(row.issued_at),
                expires_at=_as_utc(row.expires_at),
                is_current=is_current,
            ),
        )
    # Newest first so the current device is usually at the top.
    rows.sort(key=lambda r: r.issued_at, reverse=True)
    return rows


async def revoke_session(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    family_id: uuid.UUID,
) -> None:
    """Revoke a single refresh family owned by ``user_id``.

    Raises :class:`SessionNotFoundError` when the family is either
    not owned by the user or is already fully revoked — the SPA
    treats both as "stale UI; refresh".
    """

    heads = await _family_head_for_user(session, user_id=user_id)
    if family_id not in heads:
        raise SessionNotFoundError()
    await refresh_service.revoke_family(session, family_id=family_id)


async def revoke_other_sessions(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    current_family_id: uuid.UUID | None,
) -> int:
    """Revoke every active family for ``user_id`` except ``current_family_id``.

    Returns the count of revoked families. ``current_family_id=None``
    means "revoke everything" — handy as a sanity nuke from settings
    when the user has lost their cookie.
    """

    heads = await _family_head_for_user(session, user_id=user_id)
    revoked = 0
    for family_id in list(heads):
        if current_family_id is not None and family_id == current_family_id:
            continue
        await refresh_service.revoke_family(session, family_id=family_id)
        revoked += 1
    return revoked


__all__ = [
    "SessionRow",
    "list_sessions_for_user",
    "revoke_other_sessions",
    "revoke_session",
]
