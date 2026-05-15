"""Workspace business logic.

Adapted from reference project's ``services/workspaces.py``. R2 MVP
is single-tenant by default: ``ensure_default`` keeps a one-to-one
mapping between users and workspaces, plus one default brand.

R3 will flip on multi-user team mode by adding rows to
``workspace_members``; the workspace row itself doesn't change shape.
"""

from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import get_redis
from app.db.rls import set_rls_context
from app.models.brand import Brand
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceType
from app.models.workspace_member import WorkspaceMember, WorkspaceMemberRole
from app.services import memberships_cache

logger = structlog.get_logger(__name__)

DEFAULT_SLUG = "default"
DEFAULT_NAME = "Personal"
DEFAULT_BRAND_NAME = "My Brand"


async def _get_default(session: AsyncSession, owner_id: object) -> Workspace | None:
    res = await session.execute(
        select(Workspace).where(
            Workspace.owner_id == owner_id,
            Workspace.slug == DEFAULT_SLUG,
        ),
    )
    return res.scalar_one_or_none()


async def ensure_default(session: AsyncSession, user: User) -> Workspace:
    """Return the user's default workspace, creating it (+ owner
    membership + default brand) if missing.

    Idempotent: if the row already exists, it is returned unchanged.
    """

    existing = await _get_default(session, user.id)
    if existing is not None:
        return existing

    # The workspace INSERT is guarded by the ``workspaces_isolation``
    # RLS policy (D27 / D65): the row's ``owner_id`` must match
    # ``app.current_user_id``. Install that GUC up front so the
    # bootstrap path of a brand-new sign-up passes the WITH CHECK
    # predicate without needing a BYPASSRLS role.
    await set_rls_context(
        session,
        user_id=user.id,
        tenant_id=None,
        platform_role=user.platform_role,
    )

    workspace = Workspace(
        owner_id=user.id,
        name=user.full_name or DEFAULT_NAME,
        slug=DEFAULT_SLUG,
        type=WorkspaceType.SOLO,
        preferred_currency=user.preferred_currency,
    )
    session.add(workspace)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        again = await _get_default(session, user.id)
        if again is None:  # pragma: no cover - defensive
            raise
        return again

    # Now that the workspace exists, promote it to the active tenant so
    # the membership + default brand INSERTs satisfy the
    # ``workspace_id = current_tenant_id`` half of the RLS predicate.
    await set_rls_context(
        session,
        user_id=user.id,
        tenant_id=workspace.id,
        platform_role=user.platform_role,
    )

    member = WorkspaceMember(
        workspace_id=workspace.id,
        user_id=user.id,
        role=WorkspaceMemberRole.OWNER,
    )
    session.add(member)

    brand = Brand(
        workspace_id=workspace.id,
        name=DEFAULT_BRAND_NAME,
        content_language="ru",
        timezone=user.timezone,
    )
    session.add(brand)

    await session.flush()
    # D64: bust the membership cache so the next /me / authenticated
    # request rebuilds it with the freshly inserted owner row instead
    # of replaying a pre-bootstrap empty entry from a previous request.
    try:
        await memberships_cache.invalidate(get_redis(), user.id)
    except Exception as exc:  # pragma: no cover - logged for ops
        logger.warning(
            "workspaces.default_created.cache_invalidate_failed",
            user_id=str(user.id),
            error=exc.__class__.__name__,
        )
    logger.info(
        "workspaces.default_created",
        user_id=str(user.id),
        workspace_id=str(workspace.id),
        brand_id=str(brand.id),
    )
    return workspace


async def current_for_user(session: AsyncSession, user: User) -> Workspace:
    """Resolve the workspace the caller is currently acting in.

    R2 only ever has one workspace per user, so this returns the
    default workspace; a missing row is repaired on the fly.
    """

    existing = await _get_default(session, user.id)
    if existing is not None:
        return existing
    return await ensure_default(session, user)


async def list_for_user(session: AsyncSession, user: User) -> list[Workspace]:
    """Every workspace the user is a member of. Reserved for R3."""

    res = await session.execute(
        select(Workspace)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user.id)
        .order_by(Workspace.created_at.asc()),
    )
    return list(res.scalars().all())


__all__ = [
    "DEFAULT_BRAND_NAME",
    "DEFAULT_NAME",
    "DEFAULT_SLUG",
    "current_for_user",
    "ensure_default",
    "list_for_user",
]
