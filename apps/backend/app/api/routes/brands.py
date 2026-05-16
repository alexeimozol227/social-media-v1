"""Brand-listing routes (PR #14).

Mounted under ``/v1``. The single endpoint —
``GET /v1/users/me/brands`` — powers the header brand-switcher
dropdown. Authenticated; returns every brand the user's active
workspace owns, ordered with the default brand first.

R2 MVP only has one brand per workspace; the endpoint still
exists so the frontend's switcher can render the (single)
default and the SPA stays forwards-compatible with Sprint 9
multi-brand mode (docs/06-roadmap.md).
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession
from app.schemas.channels import BrandSummary
from app.services import brands as brands_service
from app.services import workspaces as workspaces_service

logger = structlog.get_logger(__name__)

router = APIRouter()


@router.get(
    "/v1/users/me/brands",
    response_model=list[BrandSummary],
    summary="Brands the current user can act on",
)
async def list_my_brands(
    db: DbSession,
    user: CurrentUser,
) -> list[BrandSummary]:
    workspace = await workspaces_service.current_for_user(db, user)
    rows = await brands_service.list_for_workspace(db, workspace.id)
    return [BrandSummary.model_validate(b) for b in rows]


__all__ = ["router"]
