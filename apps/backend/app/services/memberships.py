"""Workspace membership mutations + cache/WS invalidation (D64).

docs/04-architecture.md §18.6 + docs/05-tech-stack.md §4.5: every
``workspace_members`` mutation MUST

* run inside the caller's transaction (so we don't half-apply an
  invite + miss the invalidation if the commit later fails);
* invalidate the affected user's Redis ``user:{id}:memberships``
  cache entry;
* publish :class:`app.events.schemas.AuthRefreshRequiredEvent` on
  the user's per-user WS channel so the SPA refreshes the access
  token immediately rather than waiting for the 15-min TTL.

This module exposes the small surface every membership-changing
flow needs to call (R2 doesn't ship the admin Settings UI yet, but
the wiring is already needed by the test suite and is the contract
the upcoming Settings / Admin Panel modules will plug into). The
DB writes themselves are kept thin — the cache and WS plumbing is
what justifies a shared module rather than open-coding each mutation
inline.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.event_bus import publish_for_user
from app.core.logging import get_logger
from app.events.schemas import AuthRefreshRequiredEvent
from app.models.workspace_member import WorkspaceMember
from app.services import memberships_cache

logger = get_logger(__name__)


async def _invalidate_and_push(
    redis: Any,
    user_id: uuid.UUID,
    *,
    reason: str,
    workspace_id: uuid.UUID | None,
) -> None:
    """Drop the user's membership cache and notify their open tabs.

    Both calls are best-effort — see
    :func:`app.services.memberships_cache.invalidate` and
    :func:`app.core.event_bus.publish_for_user` for the failure
    policy. A Redis blip on either path doesn't propagate to the
    caller; the cache TTL (5 min) provides a hard upper bound on
    staleness even if every push misses.
    """

    await memberships_cache.invalidate(redis, user_id)
    await publish_for_user(
        redis,
        user_id,
        AuthRefreshRequiredEvent(
            user_id=str(user_id),
            workspace_id=str(workspace_id) if workspace_id else None,
            reason=reason,
        ),
    )


async def set_role(
    session: AsyncSession,
    redis: Any,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    role: str,
) -> WorkspaceMember:
    """Change ``user_id``'s role inside ``workspace_id``.

    Returns the updated row. Raises :class:`LookupError` if the
    member doesn't exist — the admin UI is expected to invite first
    and edit role second, so a missing row is a hard error rather
    than a silent insert.
    """

    res = await session.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        ),
    )
    row = res.scalar_one_or_none()
    if row is None:
        raise LookupError(
            f"WorkspaceMember not found for workspace={workspace_id} user={user_id}",
        )
    row.role = role
    await session.flush()
    await _invalidate_and_push(
        redis,
        user_id,
        reason="role_changed",
        workspace_id=workspace_id,
    )
    logger.info(
        "memberships.role_changed",
        workspace_id=str(workspace_id),
        user_id=str(user_id),
        role=role,
    )
    return row


async def remove(
    session: AsyncSession,
    redis: Any,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> bool:
    """Remove ``user_id`` from ``workspace_id``.

    Returns ``True`` if a row was deleted, ``False`` if there was
    nothing to delete (the caller can treat a no-op as success
    — the post-condition "user is no longer a member" is satisfied).
    """

    stmt = delete(WorkspaceMember).where(
        WorkspaceMember.workspace_id == workspace_id,
        WorkspaceMember.user_id == user_id,
    )
    # ``execution_options(synchronize_session="fetch")`` is the default;
    # ``CursorResult.rowcount`` is what we want — cast to keep mypy
    # happy with the generic ``Result`` return type.
    res = await session.execute(stmt)
    deleted = bool(getattr(res, "rowcount", 0) or 0)
    await session.flush()
    if deleted:
        await _invalidate_and_push(
            redis,
            user_id,
            reason="invite_revoked",
            workspace_id=workspace_id,
        )
        logger.info(
            "memberships.removed",
            workspace_id=str(workspace_id),
            user_id=str(user_id),
        )
    return deleted


async def add(
    session: AsyncSession,
    redis: Any,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    role: str,
    invited_by: uuid.UUID | None = None,
) -> WorkspaceMember:
    """Add ``user_id`` to ``workspace_id`` with ``role``.

    The caller is responsible for verifying the inviter has the
    right scope (workspace owner / admin) — this module assumes the
    decision has already been made.
    """

    member = WorkspaceMember(
        workspace_id=workspace_id,
        user_id=user_id,
        role=role,
        invited_by=invited_by,
    )
    session.add(member)
    await session.flush()
    await _invalidate_and_push(
        redis,
        user_id,
        reason="invited",
        workspace_id=workspace_id,
    )
    logger.info(
        "memberships.added",
        workspace_id=str(workspace_id),
        user_id=str(user_id),
        role=role,
    )
    return member


__all__ = ["add", "remove", "set_role"]
