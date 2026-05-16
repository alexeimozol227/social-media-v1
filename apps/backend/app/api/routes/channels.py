"""Channel routes (PR #14).

Mounted under ``/v1/brands/{brand_id}/channels``. Endpoints:

* ``POST   /v1/brands/{brand_id}/channels``               — connect a Telegram channel
* ``GET    /v1/brands/{brand_id}/channels``               — list connected channels
* ``DELETE /v1/brands/{brand_id}/channels/{channel_id}``  — soft-detach a channel
* ``POST   /v1/brands/{brand_id}/channels/{channel_id}/verify`` — re-run admin check

``brand_id`` is parsed from the path; we still validate the
``X-Active-Brand-Id`` header / JWT claim because the same brand
must be selected as "active" before the user can mutate channels
on it (docs/plans/phase1-sprint2-plan.md §"i18n / event bus").
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
    ChannelConnectedEvent,
    ChannelDetachedEvent,
)
from app.models.channel import Channel, WorkspaceChannel
from app.schemas.channels import (
    ChannelListResponse,
    ChannelView,
    ConnectChannelRequest,
)
from app.services import audit as audit_service
from app.services import channels as channels_service

logger = structlog.get_logger(__name__)

router = APIRouter()


def _bot_client() -> TelegramBotClient:
    """FastAPI dependency that returns the Telegram Bot adapter.

    Wrapped in a tiny function so tests can override it through
    ``app.dependency_overrides`` without touching the module-level
    singleton in :mod:`app.adapters.social.telegram_bot`.
    """

    return get_telegram_bot_client()


async def _ensure_brand_matches(
    db: object,
    *,
    workspace_id: uuid.UUID,
    path_brand_id: uuid.UUID,
    active_brand_id: uuid.UUID,
) -> None:
    """Sanity check: ``brand_id`` in the path must equal the active brand.

    docs/plans/phase1-sprint2-plan.md is explicit that the SPA must
    pick the brand as "active" before mutating it; the path id is
    the authoritative one and we reject mismatches with a typed 403
    so the SPA can re-prompt the user.
    """

    if path_brand_id != active_brand_id:
        raise BrandNotInWorkspaceError()


def _to_view(binding: WorkspaceChannel, channel: Channel) -> ChannelView:
    """Flatten the (binding, registry) tuple the service returns."""

    return ChannelView(
        id=binding.id,
        channel_id=channel.id,
        platform=channel.platform,
        external_id=channel.external_id,
        username=channel.username,
        title=channel.title,
        role=binding.role,
        bot_admin_rights=dict(binding.bot_admin_rights or {}),
        connected_at=binding.connected_at,
        disconnected_at=binding.disconnected_at,
    )


@router.post(
    "/v1/brands/{brand_id}/channels",
    response_model=ChannelView,
    status_code=status.HTTP_201_CREATED,
    summary="Connect a Telegram channel to a brand",
)
async def connect_channel(
    payload: ConnectChannelRequest,
    db: DbSession,
    user: CurrentUser,
    active_brand: ActiveBrand,
    request: Request,
    brand_id: uuid.UUID = Path(...),
    client: TelegramBotClient = Depends(_bot_client),
) -> ChannelView:
    """Connect ``payload.identifier`` to ``brand_id``.

    Flow (docs/plans/phase1-sprint2-plan.md §"Бэкенд — connect"):

    1. Validate path ``brand_id`` matches the active brand.
    2. Service layer: ``get_chat`` → admin check → registry
       upsert → ``workspace_channels`` insert.
    3. Audit + event-bus + commit (single transaction).
    """

    await _ensure_brand_matches(
        db,
        workspace_id=active_brand.workspace_id,
        path_brand_id=brand_id,
        active_brand_id=active_brand.id,
    )

    binding = await channels_service.connect(
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
        event_type="channel.connected",
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
        ChannelConnectedEvent(
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
    "/v1/brands/{brand_id}/channels",
    response_model=ChannelListResponse,
    summary="List channels connected to a brand",
)
async def list_channels(
    db: DbSession,
    user: CurrentUser,
    active_brand: ActiveBrand,
    brand_id: uuid.UUID = Path(...),
    include_disconnected: bool = False,
) -> ChannelListResponse:
    await _ensure_brand_matches(
        db,
        workspace_id=active_brand.workspace_id,
        path_brand_id=brand_id,
        active_brand_id=active_brand.id,
    )
    rows, total = await channels_service.list_for_brand(
        db,
        workspace_id=active_brand.workspace_id,
        brand_id=active_brand.id,
        include_disconnected=include_disconnected,
    )
    return ChannelListResponse(
        items=[_to_view(b, c) for (b, c) in rows],
        total=total,
    )


@router.delete(
    "/v1/brands/{brand_id}/channels/{channel_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-detach a connected channel",
)
async def detach_channel(
    db: DbSession,
    user: CurrentUser,
    active_brand: ActiveBrand,
    request: Request,
    brand_id: uuid.UUID = Path(...),
    channel_id: uuid.UUID = Path(...),
) -> Response:
    await _ensure_brand_matches(
        db,
        workspace_id=active_brand.workspace_id,
        path_brand_id=brand_id,
        active_brand_id=active_brand.id,
    )
    found = await channels_service.get_binding(
        db,
        workspace_id=active_brand.workspace_id,
        brand_id=active_brand.id,
        workspace_channel_id=channel_id,
    )
    if found is None:
        raise ChannelNotFoundError()
    binding, channel = found
    if binding.disconnected_at is not None:
        raise ChannelNotConnectedError()

    await channels_service.detach(db, binding)
    await audit_service.record(
        db,
        event_type="channel.detached",
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
        ChannelDetachedEvent(
            workspace_id=str(active_brand.workspace_id),
            brand_id=str(active_brand.id),
            user_id=str(user.id),
            channel_id=str(channel.id),
            workspace_channel_id=str(binding.id),
            platform=channel.platform,
        ),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/v1/brands/{brand_id}/channels/{channel_id}/verify",
    response_model=ChannelView,
    summary="Re-run the bot-admin check on a connected channel",
)
async def verify_channel(
    db: DbSession,
    user: CurrentUser,
    active_brand: ActiveBrand,
    request: Request,
    brand_id: uuid.UUID = Path(...),
    channel_id: uuid.UUID = Path(...),
    client: TelegramBotClient = Depends(_bot_client),
) -> ChannelView:
    await _ensure_brand_matches(
        db,
        workspace_id=active_brand.workspace_id,
        path_brand_id=brand_id,
        active_brand_id=active_brand.id,
    )
    found = await channels_service.get_binding(
        db,
        workspace_id=active_brand.workspace_id,
        brand_id=active_brand.id,
        workspace_channel_id=channel_id,
    )
    if found is None:
        raise ChannelNotFoundError()
    binding, channel = found

    await channels_service.verify(db, client, binding, channel)
    await audit_service.record(
        db,
        event_type="channel.verified",
        severity="info",
        user_id=user.id,
        workspace_id=active_brand.workspace_id,
        request=request,
        metadata={
            "brand_id": str(active_brand.id),
            "channel_id": str(channel.id),
            "workspace_channel_id": str(binding.id),
            "platform": channel.platform,
            "bot_admin_rights": dict(binding.bot_admin_rights or {}),
        },
    )
    await db.commit()
    return _to_view(binding, channel)


__all__ = ["router"]
