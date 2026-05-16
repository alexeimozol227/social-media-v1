"""Brand Dashboard v0 read-side helpers (PR #19).

docs/plans/phase1-sprint2-plan.md §"PR #19 — Brand settings + Dashboard v0":
:func:`get_recent_posts_for_brand` powers ``GET /v1/brands/{id}/dashboard``
— the "5 most recent posts" preview shown on the brand dashboard
once channels are connected.

Resolution rules:

1. Find the brand's main owned channel — oldest active
   :class:`~app.models.channel.WorkspaceChannel` with
   ``role='owned'`` and ``disconnected_at IS NULL`` (stable across
   ingest re-runs).
2. If none → return ``status='no_active_channel'`` so the SPA can
   render the "connect a channel" CTA.
3. Otherwise fetch up to ``limit`` rows from ``channel_posts``
   ordered by ``posted_at DESC, tg_message_id DESC``.
4. Zero rows → ``status='no_posts_yet'`` (channel connected, ingest
   pipeline hasn't surfaced anything yet).
5. ≥1 row → ``status='ok'`` with the rows on
   :attr:`BrandDashboardSummary.recent_posts`.

The text preview is hard-trimmed at 200 characters with a trailing
ellipsis when truncated; media-only posts return ``None`` for the
preview field so the SPA can render a "[media]" placeholder instead
of an empty string.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Literal

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.channel import Channel, ChannelPost, WorkspaceChannel

logger = structlog.get_logger(__name__)

# Max length of the post text preview surfaced on the dashboard.
TEXT_PREVIEW_MAX = 200

DashboardStatus = Literal["ok", "no_active_channel", "no_posts_yet"]


@dataclass(frozen=True)
class DashboardPostPreview:
    """Lightweight snapshot of a ``channel_posts`` row for the dashboard."""

    id: uuid.UUID
    tg_message_id: int
    text_preview: str | None
    has_media: bool
    posted_at: object  # datetime; kept Object to avoid an import in the public dataclass
    views_count: int | None


@dataclass(frozen=True)
class DashboardChannelSummary:
    """Lightweight snapshot of the brand's main owned channel."""

    binding_id: uuid.UUID
    channel_id: uuid.UUID
    title: str | None
    username: str | None
    role: str
    subscribers_count: int | None
    connected_at: object  # datetime


@dataclass(frozen=True)
class BrandDashboardSummary:
    """Result of :func:`get_recent_posts_for_brand`.

    The route layer maps this onto :class:`app.schemas.brands.BrandDashboardView`.
    Keeping the dataclass in the service module means non-HTTP callers
    (Celery digest tasks in future sprints) can reuse the helper
    without hauling the Pydantic schema with them.
    """

    status: DashboardStatus
    channel: DashboardChannelSummary | None = None
    recent_posts: list[DashboardPostPreview] = field(default_factory=list)


def _text_preview(text: str | None) -> str | None:
    """Trim ``text`` to :data:`TEXT_PREVIEW_MAX` with ``…`` on truncation."""

    if text is None:
        return None
    cleaned = text.strip()
    if not cleaned:
        return None
    if len(cleaned) <= TEXT_PREVIEW_MAX:
        return cleaned
    return cleaned[: TEXT_PREVIEW_MAX - 1].rstrip() + "…"


async def _resolve_main_owned_binding(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    brand_id: uuid.UUID,
) -> tuple[WorkspaceChannel, Channel] | None:
    """Return the brand's primary owned channel binding + registry row.

    "Primary" = oldest active ``owned`` binding (i.e. the channel the
    user connected first). MVP only allows one owned channel per
    brand, but ordering by ``connected_at ASC`` makes the helper
    correct for the Pro / Network tiers (multi-channel) without code
    changes.
    """

    res = await session.execute(
        select(WorkspaceChannel, Channel)
        .join(Channel, Channel.id == WorkspaceChannel.channel_id)
        .where(
            WorkspaceChannel.workspace_id == workspace_id,
            WorkspaceChannel.brand_id == brand_id,
            WorkspaceChannel.role == "owned",
            WorkspaceChannel.disconnected_at.is_(None),
        )
        .order_by(WorkspaceChannel.connected_at.asc())
        .limit(1),
    )
    row = res.first()
    if row is None:
        return None
    binding, channel = row
    return binding, channel


async def get_recent_posts_for_brand(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    brand_id: uuid.UUID,
    limit: int = 5,
) -> BrandDashboardSummary:
    """Return up to ``limit`` most-recent posts for the brand's main channel."""

    if limit <= 0:
        msg = "limit must be a positive integer"
        raise ValueError(msg)

    found = await _resolve_main_owned_binding(
        session,
        workspace_id=workspace_id,
        brand_id=brand_id,
    )
    if found is None:
        logger.info(
            "dashboard.no_active_channel",
            workspace_id=str(workspace_id),
            brand_id=str(brand_id),
        )
        return BrandDashboardSummary(status="no_active_channel")

    binding, channel = found
    channel_summary = DashboardChannelSummary(
        binding_id=binding.id,
        channel_id=channel.id,
        title=channel.title,
        username=channel.username,
        role=binding.role,
        subscribers_count=channel.subscribers_count,
        connected_at=binding.connected_at,
    )

    res = await session.execute(
        select(ChannelPost)
        .where(ChannelPost.channel_id == channel.id)
        .order_by(ChannelPost.posted_at.desc(), ChannelPost.tg_message_id.desc())
        .limit(limit),
    )
    rows = list(res.scalars().all())
    if not rows:
        logger.info(
            "dashboard.no_posts_yet",
            workspace_id=str(workspace_id),
            brand_id=str(brand_id),
            channel_id=str(channel.id),
        )
        return BrandDashboardSummary(status="no_posts_yet", channel=channel_summary)

    previews = [
        DashboardPostPreview(
            id=row.id,
            tg_message_id=row.tg_message_id,
            text_preview=_text_preview(row.text),
            has_media=bool(row.has_media),
            posted_at=row.posted_at,
            views_count=row.views_count,
        )
        for row in rows
    ]
    return BrandDashboardSummary(
        status="ok",
        channel=channel_summary,
        recent_posts=previews,
    )


__all__ = [
    "TEXT_PREVIEW_MAX",
    "BrandDashboardSummary",
    "DashboardChannelSummary",
    "DashboardPostPreview",
    "DashboardStatus",
    "get_recent_posts_for_brand",
]
