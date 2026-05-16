"""Brand-management routes (PR #14 + PR #19).

Mounted under ``/v1``. Endpoints:

* ``GET    /v1/users/me/brands``      — brand-switcher payload (PR #14).
* ``GET    /v1/brands``               — list every brand in the workspace.
* ``POST   /v1/brands``               — create a new brand (quota-gated).
* ``GET    /v1/brands/quota``         — effective quotas + current usage.
* ``GET    /v1/brands/{id}``          — fetch one brand.
* ``PATCH  /v1/brands/{id}``          — partial-update a brand.
* ``POST   /v1/brands/{id}/default``  — promote a brand to workspace default.
* ``DELETE /v1/brands/{id}``          — soft-delete a brand.
* ``GET    /v1/brands/{id}/dashboard``— "5 most recent posts" preview.

Every endpoint is tenant-scoped through :func:`get_current_user` (which
pins ``app.current_tenant_id`` on the SQL session so RLS policies do
the heavy lifting). The service layer additionally re-checks the
workspace boundary so a malicious caller with a hand-crafted JWT
still gets a 403 even if RLS is disabled (e.g. SQLite test path).
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Path, Request, Response, status

from app.api.deps import CurrentUser, DbSession
from app.core.event_bus import publish_for_user
from app.core.redis import get_redis
from app.errors import BrandNotInWorkspaceError
from app.events.schemas import (
    BrandCreatedEvent,
    BrandDefaultChangedEvent,
    BrandDeletedEvent,
    BrandUpdatedEvent,
)
from app.schemas.brands import (
    BrandDashboardChannelView,
    BrandDashboardPostPreview,
    BrandDashboardView,
    BrandQuotaView,
    BrandView,
    CreateBrandRequest,
    UpdateBrandRequest,
)
from app.schemas.channels import BrandSummary
from app.services import audit as audit_service
from app.services import brands as brands_service
from app.services import dashboard as dashboard_service
from app.services import workspaces as workspaces_service
from app.services.billing.plans import get_active_plan_for_workspace
from app.services.billing.quotas import resolve_for_workspace

logger = structlog.get_logger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Legacy switcher endpoint (PR #14)
# ---------------------------------------------------------------------------


@router.get(
    "/v1/users/me/brands",
    response_model=list[BrandSummary],
    summary="Brands the current user can act on (header switcher)",
)
async def list_my_brands(
    db: DbSession,
    user: CurrentUser,
) -> list[BrandSummary]:
    workspace = await workspaces_service.current_for_user(db, user)
    rows = await brands_service.list_for_workspace(db, workspace.id)
    return [BrandSummary.model_validate(b) for b in rows]


# ---------------------------------------------------------------------------
# Brand CRUD (PR #19)
# ---------------------------------------------------------------------------


@router.get(
    "/v1/brands",
    response_model=list[BrandView],
    summary="List every brand in the current workspace",
)
async def list_brands(
    db: DbSession,
    user: CurrentUser,
) -> list[BrandView]:
    """Return every non-soft-deleted brand the workspace owns.

    Default brand first, then by ``created_at`` ascending — same
    ordering as the header switcher so the SPA can compute "active
    brand" deterministically without a second SELECT.
    """

    workspace = await workspaces_service.current_for_user(db, user)
    rows = await brands_service.list_for_workspace(db, workspace.id)
    return [BrandView.model_validate(b) for b in rows]


@router.get(
    "/v1/brands/quota",
    response_model=BrandQuotaView,
    summary="Workspace brand quotas (plan baseline + active override)",
)
async def get_brand_quota(
    db: DbSession,
    user: CurrentUser,
) -> BrandQuotaView:
    """Return the effective per-workspace brand quotas + current usage.

    Drives the ``Brands: X / Y`` chip on ``/settings/brands`` and the
    "upgrade your plan" CTA when ``used_brands >= max_brands``.
    """

    workspace = await workspaces_service.current_for_user(db, user)
    plan = await get_active_plan_for_workspace(db, workspace_id=workspace.id)
    limits = await resolve_for_workspace(db, workspace_id=workspace.id, plan=plan)
    used = await brands_service.count_for_workspace(db, workspace.id)
    return BrandQuotaView(
        plan_id=limits.plan_id,
        plan_code=plan.code,
        plan_name=plan.name,
        max_brands=limits.max_brands,
        used_brands=used,
        max_posts_per_month=limits.max_posts_per_month,
        max_channels_per_brand=plan.max_channels_per_brand,
        max_competitors=plan.max_competitors,
        override_active=limits.override_id is not None,
    )


@router.post(
    "/v1/brands",
    response_model=BrandView,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new brand in the current workspace",
)
async def create_brand(
    payload: CreateBrandRequest,
    db: DbSession,
    user: CurrentUser,
    request: Request,
) -> BrandView:
    """Insert a brand row after the quota gate + default-flag bookkeeping.

    Flow:

    1. Resolve workspace + active plan + effective ``max_brands``.
    2. Delegate the INSERT (and any default-flag demotion) to
       :func:`app.services.brands.create_brand`.
    3. Audit-log + publish ``brand.created`` on the per-user channel.
    """

    workspace = await workspaces_service.current_for_user(db, user)
    plan = await get_active_plan_for_workspace(db, workspace_id=workspace.id)
    limits = await resolve_for_workspace(db, workspace_id=workspace.id, plan=plan)

    brand = await brands_service.create_brand(
        db,
        workspace_id=workspace.id,
        name=payload.name,
        content_language=payload.content_language,
        timezone=payload.timezone,
        is_default=payload.is_default,
        max_brands=limits.max_brands,
    )
    await audit_service.record(
        db,
        event_type="brand.created",
        severity="info",
        user_id=user.id,
        workspace_id=workspace.id,
        request=request,
        metadata={
            "brand_id": str(brand.id),
            "name": brand.name,
            "content_language": brand.content_language,
            "timezone": brand.timezone,
            "is_default": brand.is_default,
        },
    )
    await db.commit()
    await db.refresh(brand)

    await publish_for_user(
        get_redis(),
        user.id,
        BrandCreatedEvent(
            workspace_id=str(workspace.id),
            brand_id=str(brand.id),
            user_id=str(user.id),
            name=brand.name,
            content_language=brand.content_language,
            timezone=brand.timezone,
            is_default=brand.is_default,
        ),
    )
    return BrandView.model_validate(brand)


@router.get(
    "/v1/brands/{brand_id}",
    response_model=BrandView,
    summary="Fetch a single brand by id",
)
async def get_brand(
    db: DbSession,
    user: CurrentUser,
    brand_id: uuid.UUID = Path(...),
) -> BrandView:
    """Strict tenant-scoped lookup — returns 403 for cross-workspace ids."""

    workspace = await workspaces_service.current_for_user(db, user)
    brand = await brands_service.get_in_workspace(
        db,
        workspace_id=workspace.id,
        brand_id=brand_id,
    )
    if brand is None:
        raise BrandNotInWorkspaceError()
    return BrandView.model_validate(brand)


@router.patch(
    "/v1/brands/{brand_id}",
    response_model=BrandView,
    summary="Partial-update a brand's metadata",
)
async def update_brand(
    payload: UpdateBrandRequest,
    db: DbSession,
    user: CurrentUser,
    request: Request,
    brand_id: uuid.UUID = Path(...),
) -> BrandView:
    """PATCH semantics: every field on :class:`UpdateBrandRequest` is optional.

    ``is_default`` is deliberately NOT exposed here — flipping it
    requires the atomic swap in
    :func:`app.services.brands.set_default` so the partial unique
    index ``ux_brands_workspace_default`` is never briefly violated.
    """

    workspace = await workspaces_service.current_for_user(db, user)
    brand = await brands_service.get_in_workspace(
        db,
        workspace_id=workspace.id,
        brand_id=brand_id,
    )
    if brand is None:
        raise BrandNotInWorkspaceError()

    brand, changed = await brands_service.update_brand(
        db,
        brand=brand,
        name=payload.name,
        content_language=payload.content_language,
        timezone=payload.timezone,
    )
    if changed:
        await audit_service.record(
            db,
            event_type="brand.updated",
            severity="info",
            user_id=user.id,
            workspace_id=workspace.id,
            request=request,
            metadata={
                "brand_id": str(brand.id),
                "changed_fields": changed,
            },
        )
    await db.commit()
    await db.refresh(brand)

    if changed:
        await publish_for_user(
            get_redis(),
            user.id,
            BrandUpdatedEvent(
                workspace_id=str(workspace.id),
                brand_id=str(brand.id),
                user_id=str(user.id),
                changed_fields=changed,
                name=brand.name,
                content_language=brand.content_language,
                timezone=brand.timezone,
            ),
        )
    return BrandView.model_validate(brand)


@router.post(
    "/v1/brands/{brand_id}/default",
    response_model=BrandView,
    summary="Promote a brand to the workspace's default",
)
async def set_brand_default(
    db: DbSession,
    user: CurrentUser,
    request: Request,
    brand_id: uuid.UUID = Path(...),
) -> BrandView:
    """Atomic default-brand swap.

    Demotes the previous default in the same transaction so the
    partial unique index ``ux_brands_workspace_default`` is never
    briefly violated. Idempotent: re-promoting the current default
    returns the row unchanged and skips the audit / event-bus
    writes (verified by ``brands.default_unchanged`` log entry).
    """

    workspace = await workspaces_service.current_for_user(db, user)
    brand = await brands_service.get_in_workspace(
        db,
        workspace_id=workspace.id,
        brand_id=brand_id,
    )
    if brand is None:
        raise BrandNotInWorkspaceError()

    previous_default = await brands_service.default_for_workspace(db, workspace.id)
    was_default = previous_default is not None and previous_default.id == brand.id

    brand = await brands_service.set_default(
        db,
        workspace_id=workspace.id,
        brand=brand,
    )
    if not was_default:
        await audit_service.record(
            db,
            event_type="brand.default_changed",
            severity="info",
            user_id=user.id,
            workspace_id=workspace.id,
            request=request,
            metadata={
                "brand_id": str(brand.id),
                "previous_default_brand_id": (
                    str(previous_default.id) if previous_default is not None else None
                ),
            },
        )
    await db.commit()
    await db.refresh(brand)

    if not was_default:
        await publish_for_user(
            get_redis(),
            user.id,
            BrandDefaultChangedEvent(
                workspace_id=str(workspace.id),
                brand_id=str(brand.id),
                user_id=str(user.id),
                previous_default_brand_id=(
                    str(previous_default.id) if previous_default is not None else None
                ),
            ),
        )
    return BrandView.model_validate(brand)


@router.delete(
    "/v1/brands/{brand_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete a brand",
)
async def delete_brand(
    db: DbSession,
    user: CurrentUser,
    request: Request,
    brand_id: uuid.UUID = Path(...),
) -> Response:
    """Soft-delete a brand, blocking the default / last-brand cases.

    See :func:`app.services.brands.delete_brand` for the guard
    rails. Related channels / posts are intentionally untouched
    (partial-detach is Sprint 3) — the route layer just records the
    audit entry and publishes ``brand.deleted``.
    """

    workspace = await workspaces_service.current_for_user(db, user)
    brand = await brands_service.get_in_workspace(
        db,
        workspace_id=workspace.id,
        brand_id=brand_id,
    )
    if brand is None:
        raise BrandNotInWorkspaceError()

    await brands_service.delete_brand(
        db,
        workspace_id=workspace.id,
        brand=brand,
    )
    await audit_service.record(
        db,
        event_type="brand.deleted",
        severity="info",
        user_id=user.id,
        workspace_id=workspace.id,
        request=request,
        metadata={
            "brand_id": str(brand.id),
            "name": brand.name,
        },
    )
    await db.commit()

    await publish_for_user(
        get_redis(),
        user.id,
        BrandDeletedEvent(
            workspace_id=str(workspace.id),
            brand_id=str(brand.id),
            user_id=str(user.id),
        ),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Brand Dashboard v0 (PR #19)
# ---------------------------------------------------------------------------


@router.get(
    "/v1/brands/{brand_id}/dashboard",
    response_model=BrandDashboardView,
    summary="Brand dashboard preview — 5 most recent posts",
)
async def get_brand_dashboard(
    db: DbSession,
    user: CurrentUser,
    brand_id: uuid.UUID = Path(...),
) -> BrandDashboardView:
    """Return the brand's recent-posts preview (max 5 rows).

    Delegates to :func:`app.services.dashboard.get_recent_posts_for_brand`
    and maps the dataclass result onto the public Pydantic schema.
    """

    workspace = await workspaces_service.current_for_user(db, user)
    brand = await brands_service.get_in_workspace(
        db,
        workspace_id=workspace.id,
        brand_id=brand_id,
    )
    if brand is None:
        raise BrandNotInWorkspaceError()

    summary = await dashboard_service.get_recent_posts_for_brand(
        db,
        workspace_id=workspace.id,
        brand_id=brand.id,
        limit=5,
    )

    channel_view: BrandDashboardChannelView | None = None
    if summary.channel is not None:
        ch = summary.channel
        channel_view = BrandDashboardChannelView(
            id=ch.binding_id,
            channel_id=ch.channel_id,
            title=ch.title,
            username=ch.username,
            role=ch.role,
            subscribers_count=ch.subscribers_count,
            connected_at=ch.connected_at,  # type: ignore[arg-type]
        )

    posts = [
        BrandDashboardPostPreview(
            id=row.id,
            tg_message_id=row.tg_message_id,
            text_preview=row.text_preview,
            has_media=row.has_media,
            posted_at=row.posted_at,  # type: ignore[arg-type]
            views_count=row.views_count,
        )
        for row in summary.recent_posts
    ]

    return BrandDashboardView(
        status=summary.status,
        channel=channel_view,
        recent_posts=posts,
    )


__all__ = ["router"]
