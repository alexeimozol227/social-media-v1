"""Competitor channel connect / list / detach business logic (PR #18).

docs/plans/phase1-sprint2-plan.md PR #18: the read-only flip side of
:mod:`app.services.channels`. ``workspace_channels.role='competitor'``
binds a brand to a public Telegram channel **without** requiring the
bot to be promoted as admin — the user-bot pool will crawl the
history (Sprint 3) so we never need the bot in the chat.

Key differences vs. :func:`app.services.channels.connect`:

* No ``get_chat_member`` admin probe — the bot is irrelevant for
  competitors.
* ``info.is_public`` must be ``True``: private channels can't be
  crawled by a user-bot that isn't a member, and adding the
  user-bot as a member would require operator credentials we don't
  have. Surfaces as :class:`CompetitorNotPublicError` (409).
* ``bot_admin_rights`` is forced to ``{}`` — the column is NOT NULL
  on the migration; keeping it as empty JSON is cleaner than an
  ALTER migration to make it nullable for a single role variant.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.social import (
    ChannelInfo,
    TelegramBotClient,
    TelegramChannelNotFoundError,
    TelegramTransportError,
)
from app.errors import (
    ChannelNotConnectedError,
    ChannelNotFoundError,
    CompetitorAlreadyConnectedError,
    CompetitorNotPublicError,
    TelegramAPIError,
)
from app.models.channel import (
    Channel,
    WorkspaceChannel,
    WorkspaceChannelRoleValues,
)

logger = structlog.get_logger(__name__)


def _now() -> datetime:
    return datetime.now(tz=UTC)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _resolve_channel_info(
    client: TelegramBotClient,
    identifier: str | int,
) -> ChannelInfo:
    """Mirror of :func:`app.services.channels._resolve_channel_info`.

    Kept inline (instead of imported from :mod:`channels`) so the
    competitor service stays a thin module without a private cross
    import to a sibling.
    """

    try:
        return await client.get_chat(identifier)
    except TelegramChannelNotFoundError as exc:
        raise ChannelNotFoundError() from exc
    except TelegramTransportError as exc:
        raise TelegramAPIError() from exc


async def _upsert_registry(
    session: AsyncSession,
    info: ChannelInfo,
    *,
    platform: str = "telegram",
) -> Channel:
    """Upsert ``(platform, external_id)`` row in the global registry.

    Same shape as the OWNED-channels upsert in :mod:`app.services.channels`
    — duplicated here so the two services don't reach into each
    other's private helpers. The duplication is acceptable for two
    small functions; if a third caller emerges we'll pull the helper
    into :mod:`app.services.channels_registry`.
    """

    res = await session.execute(
        select(Channel).where(
            Channel.platform == platform,
            Channel.external_id == info.chat_id,
        ),
    )
    existing = res.scalar_one_or_none()
    if existing is not None:
        existing.title = info.title
        existing.username = info.username
        existing.description = info.description
        existing.is_public = info.is_public
        existing.last_seen_at = _now()
        if info.subscribers_count is not None:
            existing.subscribers_count = info.subscribers_count
        await session.flush()
        return existing

    row = Channel(
        platform=platform,
        external_id=info.chat_id,
        username=info.username,
        title=info.title,
        description=info.description,
        is_public=info.is_public,
        subscribers_count=info.subscribers_count,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        res = await session.execute(
            select(Channel).where(
                Channel.platform == platform,
                Channel.external_id == info.chat_id,
            ),
        )
        winner = res.scalar_one_or_none()
        if winner is None:  # pragma: no cover - defensive
            raise
        return winner
    return row


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def connect_competitor(
    session: AsyncSession,
    client: TelegramBotClient,
    *,
    workspace_id: uuid.UUID,
    brand_id: uuid.UUID,
    identifier: str | int,
    platform: str = "telegram",
) -> WorkspaceChannel:
    """Resolve and bind a public competitor channel to ``brand_id``.

    Flow:

    1. ``get_chat`` — Bot API returns public-channel metadata even
       without the bot being a member.
    2. Reject private channels with :class:`CompetitorNotPublicError`.
    3. Upsert in the Global Channel Registry.
    4. Insert ``workspace_channels`` with ``role='competitor'``,
       ``bot_admin_rights={}``. Reconnect a soft-detached row lifts
       ``disconnected_at`` instead of creating a duplicate (mirrors
       :func:`app.services.channels.connect`).
    5. Raise :class:`CompetitorAlreadyConnectedError` for a live
       binding from a previous successful call.
    """

    info = await _resolve_channel_info(client, identifier)
    if not info.is_public:
        raise CompetitorNotPublicError(
            details={
                "external_id": info.chat_id,
                "title": info.title,
            },
        )

    channel = await _upsert_registry(session, info, platform=platform)

    res = await session.execute(
        select(WorkspaceChannel).where(
            WorkspaceChannel.workspace_id == workspace_id,
            WorkspaceChannel.brand_id == brand_id,
            WorkspaceChannel.channel_id == channel.id,
        ),
    )
    existing = res.scalar_one_or_none()
    if existing is not None and existing.disconnected_at is None:
        raise CompetitorAlreadyConnectedError()
    if existing is not None and existing.disconnected_at is not None:
        existing.disconnected_at = None
        existing.connected_at = _now()
        existing.role = WorkspaceChannelRoleValues.COMPETITOR
        existing.bot_admin_rights = {}
        await session.flush()
        return existing

    binding = WorkspaceChannel(
        workspace_id=workspace_id,
        brand_id=brand_id,
        channel_id=channel.id,
        role=WorkspaceChannelRoleValues.COMPETITOR,
        bot_admin_rights={},
    )
    session.add(binding)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise CompetitorAlreadyConnectedError() from exc
    return binding


async def list_competitors_for_brand(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    brand_id: uuid.UUID,
    include_disconnected: bool = False,
) -> tuple[list[tuple[WorkspaceChannel, Channel]], int]:
    """Return the brand's competitor bindings + total count.

    Filters by ``role='competitor'`` so the owned-channels list and
    the competitor list don't overlap.
    """

    base = (
        select(WorkspaceChannel, Channel)
        .join(Channel, Channel.id == WorkspaceChannel.channel_id)
        .where(
            WorkspaceChannel.workspace_id == workspace_id,
            WorkspaceChannel.brand_id == brand_id,
            WorkspaceChannel.role == WorkspaceChannelRoleValues.COMPETITOR,
        )
        .order_by(WorkspaceChannel.connected_at.desc())
    )
    if not include_disconnected:
        base = base.where(WorkspaceChannel.disconnected_at.is_(None))
    res = await session.execute(base)
    rows = [(row[0], row[1]) for row in res.all()]

    count_q = select(func.count(WorkspaceChannel.id)).where(
        WorkspaceChannel.workspace_id == workspace_id,
        WorkspaceChannel.brand_id == brand_id,
        WorkspaceChannel.role == WorkspaceChannelRoleValues.COMPETITOR,
    )
    if not include_disconnected:
        count_q = count_q.where(WorkspaceChannel.disconnected_at.is_(None))
    total = (await session.execute(count_q)).scalar_one()
    return rows, int(total or 0)


async def get_competitor_binding(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    brand_id: uuid.UUID,
    workspace_channel_id: uuid.UUID,
) -> tuple[WorkspaceChannel, Channel] | None:
    """Look up a competitor binding by its row id.

    Filtered by ``role='competitor'`` so the channels route's
    ``get_binding`` and this one can't be confused — calling
    ``DELETE /v1/brands/{id}/competitors/{wc_id}`` against a
    non-competitor binding 404s.
    """

    res = await session.execute(
        select(WorkspaceChannel, Channel)
        .join(Channel, Channel.id == WorkspaceChannel.channel_id)
        .where(
            WorkspaceChannel.id == workspace_channel_id,
            WorkspaceChannel.workspace_id == workspace_id,
            WorkspaceChannel.brand_id == brand_id,
            WorkspaceChannel.role == WorkspaceChannelRoleValues.COMPETITOR,
        ),
    )
    row = res.first()
    if row is None:
        return None
    return row[0], row[1]


async def detach_competitor(
    session: AsyncSession,
    binding: WorkspaceChannel,
) -> WorkspaceChannel:
    """Soft-detach a competitor binding. Mirrors
    :func:`app.services.channels.detach`."""

    if binding.disconnected_at is not None:
        raise ChannelNotConnectedError()
    binding.disconnected_at = _now()
    await session.flush()
    return binding


__all__ = [
    "connect_competitor",
    "detach_competitor",
    "get_competitor_binding",
    "list_competitors_for_brand",
]
