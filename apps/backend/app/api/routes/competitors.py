"""Competitor channel routes (PR #18).

Mounted under ``/v1/brands/{brand_id}/competitors``. Endpoints:

* ``POST   /v1/brands/{brand_id}/competitors`` — connect a public
  competitor channel (role=``competitor``, no admin required).
* ``GET    /v1/brands/{brand_id}/competitors`` — list connected
  competitor bindings.
* ``DELETE /v1/brands/{brand_id}/competitors/{workspace_channel_id}``
  — soft-detach.

Mirrors :mod:`app.api.routes.channels` but uses the
:mod:`app.services.competitors` service to enforce ``role='competitor'``
+ ``bot_admin_rights={}``.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, Path, Request, Response, status

from app.adapters.social import TelegramBotClient, get_telegram_bot_client
from app.api.deps import (
    ActiveBrand,
    CurrentUser,
    DbSession,
)
from app.core.event_bus import publish_for_user
from app.core.redis import get_redis
from app.errors import (
    BrandNotInWorkspaceError,
    ChannelNotConnectedError,
    ChannelNotFoundError,
)
from app.events.schemas import (
    CompetitorConnectedEvent,
    CompetitorDetachedEvent,
)
from app.models.channel import Channel, WorkspaceChannel
from app.schemas.competitors import (
    CompetitorListResponse,
    CompetitorView,
    ConnectCompetitorRequest,
)
from app.services import audit as audit_service
from app.services import competitors as competitors_service

logger = structlog.get_logger(__name__)

router = APIRouter()


def _bot_client() -> TelegramBotClient:
    """Identical to :func:`app.api.routes.channels._bot_client` —
    re-declared per module so the dependency override hooks per
    router (the channels-router override mustn't affect competitor
    tests, and vice versa).
    """

    return get_telegram_bot_client()


async def _ensure_brand_matches(
    *,
    path_brand_id: uuid.UUID,
    active_brand_id: uuid.UUID,
) -> None:
    """Sanity check: ``brand_id`` in the path must equal the active brand.

    Mirrors the helper in :mod:`app.api.routes.channels` — duplicated
    here so the route module is self-contained.
    """

    if path_brand_id != active_brand_id:
        raise BrandNotInWorkspaceError()


def _to_view(binding: WorkspaceChannel, channel: Channel) -> CompetitorView:
    """Flatten the (binding, registry) tuple the service returns."""

    return CompetitorView(
        id=binding.id,
        channel_id=channel.id,
        platform=channel.platform,
        external_id=channel.external_id,
        username=channel.username,
        title=channel.title,
        role="competitor",
        connected_at=binding.connected_at,
        disconnected_at=binding.disconnected_at,
    )


@router.post(
    "/v1/brands/{brand_id}/competitors",
    response_model=CompetitorView,
    status_code=status.HTTP_201_CREATED,
    summary="Connect a public competitor channel to a brand",
)
async def connect_competitor(
    payload: ConnectCompetitorRequest,
    db: DbSession,
    user: CurrentUser,
    active_brand: ActiveBrand,
    request: Request,
    brand_id: uuid.UUID = Path(...),
    client: TelegramBotClient = Depends(_bot_client),
) -> CompetitorView:
    """Bind ``payload.identifier`` to ``brand_id`` as a competitor.

    Flow:

    1. Validate path ``brand_id`` matches the active brand.
    2. Service layer: ``get_chat`` → public check → registry upsert
       → ``workspace_channels`` insert with ``role='competitor'``.
    3. Audit + event-bus + commit.
    """

    await _ensure_brand_matches(
        path_brand_id=brand_id,
        active_brand_id=active_brand.id,
    )

    binding = await competitors_service.connect_competitor(
        db,
        client,
        workspace_id=active_brand.workspace_id,
        brand_id=active_brand.id,
        identifier=payload.identifier,
        platform=payload.platform,
    )
    channel = await db.get(Channel, binding.channel_id)
    if channel is None:  # pragma: no cover - defensive
        raise ChannelNotFoundError()

    await audit_service.record(
        db,
        event_type="competitor.connected",
        severity="info",
        user_id=user.id,
        workspace_id=active_brand.workspace_id,
        request=request,
        metadata={
            "brand_id": str(active_brand.id),
            "channel_id": str(channel.id),
            "workspace_channel_id": str(binding.id),
            "platform": channel.platform,
            "external_id": channel.external_id,
            "username": channel.username,
        },
    )
    await db.commit()

    await publish_for_user(
        get_redis(),
        user.id,
        CompetitorConnectedEvent(
            workspace_id=str(active_brand.workspace_id),
            brand_id=str(active_brand.id),
            user_id=str(user.id),
            channel_id=str(channel.id),
            workspace_channel_id=str(binding.id),
            platform=channel.platform,
            title=channel.title,
            username=channel.username,
        ),
    )
    return _to_view(binding, channel)


@router.get(
    "/v1/brands/{brand_id}/competitors",
    response_model=CompetitorListResponse,
    summary="List competitor channels connected to a brand",
)
async def list_competitors(
    db: DbSession,
    user: CurrentUser,
    active_brand: ActiveBrand,
    brand_id: uuid.UUID = Path(...),
    include_disconnected: bool = False,
) -> CompetitorListResponse:
    await _ensure_brand_matches(
        path_brand_id=brand_id,
        active_brand_id=active_brand.id,
    )
    rows, total = await competitors_service.list_competitors_for_brand(
        db,
        workspace_id=active_brand.workspace_id,
        brand_id=active_brand.id,
        include_disconnected=include_disconnected,
    )
    return CompetitorListResponse(
        items=[_to_view(b, c) for (b, c) in rows],
        total=total,
    )


@router.delete(
    "/v1/brands/{brand_id}/competitors/{workspace_channel_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-detach a competitor channel binding",
)
async def detach_competitor(
    db: DbSession,
    user: CurrentUser,
    active_brand: ActiveBrand,
    request: Request,
    brand_id: uuid.UUID = Path(...),
    workspace_channel_id: uuid.UUID = Path(...),
) -> Response:
    await _ensure_brand_matches(
        path_brand_id=brand_id,
        active_brand_id=active_brand.id,
    )
    found = await competitors_service.get_competitor_binding(
        db,
        workspace_id=active_brand.workspace_id,
        brand_id=active_brand.id,
        workspace_channel_id=workspace_channel_id,
    )
    if found is None:
        raise ChannelNotFoundError()
    binding, channel = found
    if binding.disconnected_at is not None:
        raise ChannelNotConnectedError()

    await competitors_service.detach_competitor(db, binding)
    await audit_service.record(
        db,
        event_type="competitor.detached",
        severity="info",
        user_id=user.id,
        workspace_id=active_brand.workspace_id,
        request=request,
        metadata={
            "brand_id": str(active_brand.id),
            "channel_id": str(channel.id),
            "workspace_channel_id": str(binding.id),
            "platform": channel.platform,
        },
    )
    await db.commit()

    await publish_for_user(
        get_redis(),
        user.id,
        CompetitorDetachedEvent(
            workspace_id=str(active_brand.workspace_id),
            brand_id=str(active_brand.id),
            user_id=str(user.id),
            channel_id=str(channel.id),
            workspace_channel_id=str(binding.id),
            platform=channel.platform,
        ),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
