"""Brand Memory routes (PR #21).

docs/plans/phase1-sprint3-plan.md §"PR #21 — Brand Memory v0".

Endpoints (all mounted under ``/v1`` and tenant-scoped through
:func:`app.api.deps.get_current_user` which pins ``app.current_tenant_id``
on the SQL session so RLS does the heavy lifting):

* ``GET   /v1/brands/{brand_id}/memory/core``
* ``PATCH /v1/brands/{brand_id}/memory/core``
* ``GET   /v1/brands/{brand_id}/memory/effective``
* ``GET   /v1/brands/{brand_id}/memory/examples``
* ``GET   /v1/brands/{brand_id}/memory/overlays/{ws_channel_id}``
* ``PATCH /v1/brands/{brand_id}/memory/overlays/{ws_channel_id}``

The route layer is responsible for:

* tenant boundary re-check (mirrors :mod:`app.api.routes.brands`),
* committing the unit of work,
* audit logging,
* publishing the lifecycle event on the per-user WS channel.

The Brand Memory service handles cache reads/writes, version checks,
and payload validation.
"""

from __future__ import annotations

import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Path, Query, Request

from app.api.deps import CurrentUser, DbSession
from app.core.event_bus import publish_for_user
from app.core.redis import get_redis
from app.errors import BrandNotInWorkspaceError
from app.events.schemas import (
    BrandMemoryCoreUpdatedEvent,
    BrandMemoryOverlayUpdatedEvent,
)
from app.schemas.brand_memory import (
    BrandMemoryCoreView,
    BrandMemoryExampleList,
    BrandMemoryExampleView,
    BrandMemoryOverlayView,
    BrandMemoryPayload,
    EffectiveBrandMemoryView,
    UpdateBrandMemoryCoreRequest,
    UpdateBrandMemoryOverlayRequest,
)
from app.services import audit as audit_service
from app.services import brand_memory as brand_memory_service
from app.services import brands as brands_service
from app.services import workspaces as workspaces_service

logger = structlog.get_logger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _resolve_brand(
    db: DbSession,
    user: CurrentUser,
    brand_id: uuid.UUID,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Tenant-scope check; return ``(workspace_id, brand_id)``.

    Mirrors the gate used by every route in
    :mod:`app.api.routes.brands` so a malicious caller with a
    hand-crafted JWT gets the same 403 regardless of which Brand-related
    surface they probe.
    """

    workspace = await workspaces_service.current_for_user(db, user)
    brand = await brands_service.get_in_workspace(
        db,
        workspace_id=workspace.id,
        brand_id=brand_id,
    )
    if brand is None:
        raise BrandNotInWorkspaceError()
    return workspace.id, brand.id


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------


@router.get(
    "/v1/brands/{brand_id}/memory/core",
    response_model=BrandMemoryCoreView,
    summary="Fetch the brand's canonical Brand Memory core payload",
)
async def get_brand_memory_core(
    db: DbSession,
    user: CurrentUser,
    brand_id: Annotated[uuid.UUID, Path(...)],
) -> BrandMemoryCoreView:
    """Return the brand's canonical core payload.

    On first read for a brand the service materialises an empty row
    so the SPA always sees a stable ``version`` to echo on PATCH.
    """

    workspace_id, brand_id = await _resolve_brand(db, user, brand_id)
    snapshot = await brand_memory_service.get_core(
        db,
        get_redis(),
        brand_id=brand_id,
        workspace_id=workspace_id,
    )
    # First-read materialisation flushes via the service; commit so
    # the placeholder row survives the request boundary.
    await db.commit()
    return BrandMemoryCoreView(
        brand_id=snapshot.brand_id,
        payload=BrandMemoryPayload.model_validate(snapshot.payload),
        version=snapshot.version,
        updated_by_user_id=snapshot.updated_by_user_id,
        updated_by_agent=snapshot.updated_by_agent,
        created_at=snapshot.created_at,
        updated_at=snapshot.updated_at,
    )


@router.patch(
    "/v1/brands/{brand_id}/memory/core",
    response_model=BrandMemoryCoreView,
    summary="Replace the brand's canonical Brand Memory core payload",
)
async def update_brand_memory_core(
    payload: UpdateBrandMemoryCoreRequest,
    db: DbSession,
    user: CurrentUser,
    request: Request,
    brand_id: Annotated[uuid.UUID, Path(...)],
) -> BrandMemoryCoreView:
    """Version-checked PATCH; replaces the core payload wholesale."""

    workspace_id, brand_id = await _resolve_brand(db, user, brand_id)

    # ``exclude_unset=True`` keeps the stored payload sparse — only keys
    # the caller explicitly sent survive into the row. Combined with
    # the overlay's shallow merge, this is what gives the SPA "missing
    # keys fall through to the core" semantics.
    new_payload = payload.payload.model_dump(mode="json", exclude_unset=True)
    snapshot = await brand_memory_service.update_core(
        db,
        get_redis(),
        brand_id=brand_id,
        workspace_id=workspace_id,
        payload=new_payload,
        if_match_version=payload.if_match_version,
        updated_by_user_id=user.id,
        updated_by_agent=None,
    )
    await audit_service.record(
        db,
        event_type="brand_memory.core_updated",
        severity="info",
        user_id=user.id,
        workspace_id=workspace_id,
        request=request,
        metadata={
            "brand_id": str(brand_id),
            "version": snapshot.version,
        },
    )
    await db.commit()

    await publish_for_user(
        get_redis(),
        user.id,
        BrandMemoryCoreUpdatedEvent(
            workspace_id=str(workspace_id),
            brand_id=str(brand_id),
            user_id=str(user.id),
            version=snapshot.version,
            updated_by_agent=None,
        ),
    )
    return BrandMemoryCoreView(
        brand_id=snapshot.brand_id,
        payload=BrandMemoryPayload.model_validate(snapshot.payload),
        version=snapshot.version,
        updated_by_user_id=snapshot.updated_by_user_id,
        updated_by_agent=snapshot.updated_by_agent,
        created_at=snapshot.created_at,
        updated_at=snapshot.updated_at,
    )


# ---------------------------------------------------------------------------
# Effective merge
# ---------------------------------------------------------------------------


@router.get(
    "/v1/brands/{brand_id}/memory/effective",
    response_model=EffectiveBrandMemoryView,
    summary="Fetch the effective Brand Memory payload (core + overlay)",
)
async def get_brand_memory_effective(
    db: DbSession,
    user: CurrentUser,
    brand_id: Annotated[uuid.UUID, Path(...)],
    workspace_channel_id: Annotated[
        uuid.UUID | None,
        Query(
            alias="workspaceChannelId",
            description=(
                "Optional ``workspace_channels.id``. When supplied, the "
                "matching overlay (if any) is merged on top of the core "
                "payload; otherwise the response is the core payload alone."
            ),
        ),
    ] = None,
) -> EffectiveBrandMemoryView:
    """Return the effective Brand Memory payload for the (brand, channel).

    The route never raises for a missing overlay — a brand without an
    overlay row returns its core payload with ``workspace_channel_id=None``
    so the Content Agent has a single-shot endpoint to read from.
    """

    workspace_id, brand_id = await _resolve_brand(db, user, brand_id)

    effective = await brand_memory_service.get_effective(
        db,
        get_redis(),
        brand_id=brand_id,
        workspace_id=workspace_id,
        workspace_channel_id=workspace_channel_id,
    )
    await db.commit()
    return EffectiveBrandMemoryView(
        brand_id=effective.brand_id,
        workspace_channel_id=effective.workspace_channel_id,
        payload=BrandMemoryPayload.model_validate(effective.payload),
        core_version=effective.core_version,
        overlay_version=effective.overlay_version,
    )


# ---------------------------------------------------------------------------
# Examples
# ---------------------------------------------------------------------------


@router.get(
    "/v1/brands/{brand_id}/memory/examples",
    response_model=BrandMemoryExampleList,
    summary="List vector-indexed Brand Memory example snippets",
)
async def list_brand_memory_examples(
    db: DbSession,
    user: CurrentUser,
    brand_id: Annotated[uuid.UUID, Path(...)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> BrandMemoryExampleList:
    """Return ``items`` (most-recent first) + ``total`` for the brand.

    The vector column itself is intentionally not in the response —
    the SPA only renders ``text_snippet`` previews; full vectors are
    consumed server-side by the Content Agent in PR #25.
    """

    _, brand_id = await _resolve_brand(db, user, brand_id)
    rows, total = await brand_memory_service.list_examples(
        db,
        brand_id=brand_id,
        limit=limit,
        offset=offset,
    )
    return BrandMemoryExampleList(
        items=[BrandMemoryExampleView.model_validate(row) for row in rows],
        total=total,
    )


# ---------------------------------------------------------------------------
# Overlays
# ---------------------------------------------------------------------------


@router.get(
    "/v1/brands/{brand_id}/memory/overlays/{workspace_channel_id}",
    response_model=BrandMemoryOverlayView,
    summary="Fetch the per-channel Brand Memory overlay",
)
async def get_brand_memory_overlay(
    db: DbSession,
    user: CurrentUser,
    brand_id: Annotated[uuid.UUID, Path(...)],
    workspace_channel_id: Annotated[uuid.UUID, Path(...)],
) -> BrandMemoryOverlayView:
    """Return the per-channel overlay; materialises empty on first read.

    Matches :func:`get_brand_memory_core` semantics — the SPA always
    has a row to PATCH against, with a stable ``version=1`` placeholder.
    """

    workspace_id, brand_id = await _resolve_brand(db, user, brand_id)
    snapshot = await brand_memory_service.get_overlay(
        db,
        get_redis(),
        brand_id=brand_id,
        workspace_channel_id=workspace_channel_id,
    )
    if snapshot is None:
        # Materialise an empty overlay so the SPA always has a row to
        # PATCH against. We treat the first GET as an idempotent
        # placeholder insert; subsequent PATCHes bump from version=1.
        snapshot = await brand_memory_service.update_overlay(
            db,
            get_redis(),
            brand_id=brand_id,
            workspace_id=workspace_id,
            workspace_channel_id=workspace_channel_id,
            payload={},
            if_match_version=None,
            updated_by_user_id=None,
            updated_by_agent=None,
        )
    await db.commit()
    return BrandMemoryOverlayView(
        brand_id=snapshot.brand_id,
        workspace_channel_id=snapshot.workspace_channel_id,
        payload=BrandMemoryPayload.model_validate(snapshot.payload),
        version=snapshot.version,
        updated_by_user_id=snapshot.updated_by_user_id,
        updated_by_agent=snapshot.updated_by_agent,
        created_at=snapshot.created_at,
        updated_at=snapshot.updated_at,
    )


@router.patch(
    "/v1/brands/{brand_id}/memory/overlays/{workspace_channel_id}",
    response_model=BrandMemoryOverlayView,
    summary="Replace the per-channel Brand Memory overlay",
)
async def update_brand_memory_overlay(
    payload: UpdateBrandMemoryOverlayRequest,
    db: DbSession,
    user: CurrentUser,
    request: Request,
    brand_id: Annotated[uuid.UUID, Path(...)],
    workspace_channel_id: Annotated[uuid.UUID, Path(...)],
) -> BrandMemoryOverlayView:
    """Version-checked PATCH; upserts the per-channel overlay."""

    workspace_id, brand_id = await _resolve_brand(db, user, brand_id)

    # See :func:`update_brand_memory_core` for the rationale —
    # ``exclude_unset=True`` keeps overlay rows sparse so the
    # effective-merge route falls through to the core for unset keys.
    new_payload = payload.payload.model_dump(mode="json", exclude_unset=True)
    snapshot = await brand_memory_service.update_overlay(
        db,
        get_redis(),
        brand_id=brand_id,
        workspace_id=workspace_id,
        workspace_channel_id=workspace_channel_id,
        payload=new_payload,
        if_match_version=payload.if_match_version,
        updated_by_user_id=user.id,
        updated_by_agent=None,
    )
    await audit_service.record(
        db,
        event_type="brand_memory.overlay_updated",
        severity="info",
        user_id=user.id,
        workspace_id=workspace_id,
        request=request,
        metadata={
            "brand_id": str(brand_id),
            "workspace_channel_id": str(workspace_channel_id),
            "version": snapshot.version,
        },
    )
    await db.commit()

    await publish_for_user(
        get_redis(),
        user.id,
        BrandMemoryOverlayUpdatedEvent(
            workspace_id=str(workspace_id),
            brand_id=str(brand_id),
            user_id=str(user.id),
            workspace_channel_id=str(workspace_channel_id),
            version=snapshot.version,
            updated_by_agent=None,
        ),
    )
    return BrandMemoryOverlayView(
        brand_id=snapshot.brand_id,
        workspace_channel_id=snapshot.workspace_channel_id,
        payload=BrandMemoryPayload.model_validate(snapshot.payload),
        version=snapshot.version,
        updated_by_user_id=snapshot.updated_by_user_id,
        updated_by_agent=snapshot.updated_by_agent,
        created_at=snapshot.created_at,
        updated_at=snapshot.updated_at,
    )


__all__ = ["router"]
