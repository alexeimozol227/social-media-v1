"""Brand business logic.

docs/04-architecture.md §3 + docs/plans/phase1-sprint2-plan.md
(PR #14): the brand is the unit every business object hangs off
(channels, content plan, posts). PR #14 only needs read-side
helpers used by the active-brand dependency and the connect-channel
flow:

* :func:`default_for_workspace` — returns the workspace's canonical
  brand (``brands.is_default = TRUE``), backfilling the flag on the
  oldest brand of a legacy workspace that doesn't have a default
  yet.
* :func:`get_in_workspace` — strict tenant-scoped lookup; returns
  ``None`` when ``brand_id`` doesn't belong to ``workspace_id`` so
  the API layer can map that to a typed
  :class:`app.errors.BrandNotInWorkspaceError`.
* :func:`list_for_workspace` — every brand the workspace owns,
  ordered with default first (handy for the brand-switcher UI).

R2 MVP creates exactly one brand per workspace at sign-up
(``services.workspaces.ensure_default``); these helpers stay
correct when Sprint 9 introduces multi-brand mode.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.brand import Brand

logger = structlog.get_logger(__name__)


async def default_for_workspace(
    session: AsyncSession,
    workspace_id: uuid.UUID,
) -> Brand | None:
    """Return the workspace's default brand, repairing the flag if missing.

    Legacy workspaces created before PR #14 didn't have
    ``is_default`` — to keep that path correct we fall back to the
    oldest brand and flip its flag so subsequent calls return
    instantly. Brand-new workspaces created on or after PR #14
    already get ``is_default=True`` from
    :func:`app.services.workspaces.ensure_default`.

    Returns ``None`` only if the workspace has zero brands, which
    should never happen for a workspace that finished sign-up
    bootstrap.
    """

    res = await session.execute(
        select(Brand)
        .where(
            Brand.workspace_id == workspace_id,
            Brand.deleted_at.is_(None),
            Brand.is_default.is_(True),
        )
        .order_by(Brand.created_at.asc())
        .limit(1),
    )
    found = res.scalar_one_or_none()
    if found is not None:
        return found

    # Backfill path: pick the oldest brand and promote it. The
    # partial-unique index ``ux_brands_workspace_default`` makes
    # this a single safe write per workspace.
    res = await session.execute(
        select(Brand)
        .where(
            Brand.workspace_id == workspace_id,
            Brand.deleted_at.is_(None),
        )
        .order_by(Brand.created_at.asc())
        .limit(1),
    )
    oldest = res.scalar_one_or_none()
    if oldest is None:
        return None
    oldest.is_default = True
    await session.flush()
    logger.info(
        "brands.default_backfilled",
        workspace_id=str(workspace_id),
        brand_id=str(oldest.id),
    )
    return oldest


async def get_in_workspace(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    brand_id: uuid.UUID,
) -> Brand | None:
    """Strict tenant-scoped lookup.

    Returns ``None`` when ``brand_id`` doesn't belong to
    ``workspace_id`` (or doesn't exist). Caller maps that to a
    typed 403 — keeping the same response shape whether the brand
    is missing, soft-deleted, or owned by someone else, so a
    probe can't enumerate brand ids across workspaces.
    """

    res = await session.execute(
        select(Brand).where(
            Brand.id == brand_id,
            Brand.workspace_id == workspace_id,
            Brand.deleted_at.is_(None),
        ),
    )
    return res.scalar_one_or_none()


async def list_for_workspace(
    session: AsyncSession,
    workspace_id: uuid.UUID,
) -> list[Brand]:
    """Every brand the workspace owns, default-first then by ``created_at``.

    Used by the header brand-switcher UI (``GET /v1/users/me/brands``)
    and the admin lens.
    """

    res = await session.execute(
        select(Brand)
        .where(
            Brand.workspace_id == workspace_id,
            Brand.deleted_at.is_(None),
        )
        .order_by(Brand.is_default.desc(), Brand.created_at.asc()),
    )
    return list(res.scalars().all())


__all__ = [
    "default_for_workspace",
    "get_in_workspace",
    "list_for_workspace",
]
