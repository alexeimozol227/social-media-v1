"""Live channel-post ingest business logic (PR #16).

docs/plans/phase1-sprint2-plan.md PR #16: every webhook delivery
from Telegram lands here after :mod:`app.integrations.telegram.
parsers` has shaped it into a :class:`ChannelPostSnapshot`. The
service is responsible for:

* resolving the ``Channel`` row by ``(platform, external_id)`` \u2014
  unknown channels are silently dropped so a webhook for a channel
  no workspace ever connected doesn't pollute audit logs;
* deduping new posts via :func:`ingest_snapshots` (reuses the
  PR #15 transactional dedup);
* upserting edited posts \u2014 ``Message.edit_date`` updates surface
  here when Telegram replays the post with new content;
* publishing ``ChannelPostReceivedEvent`` / ``ChannelPostEditedEvent``
  to every workspace_member of every binding owning the channel.

Multi-tenant fan-out
--------------------
A single channel can be connected from multiple workspaces (PR #14
introduced the Global Channel Registry exactly so we don't pay the
ingest cost N times). The Brand Memory updater + dashboard need a
notification per *binding*, so we walk every ``workspace_channels``
row that points at the resolved ``channel_id`` and publish an event
to its workspace member. RLS isolation stays intact because the
event-bus channel is keyed by ``user_id`` \u2014 each tab only ever
subscribes to its own user channel.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.social import ChannelPostSnapshot
from app.core.event_bus import publish_for_user
from app.events.schemas import (
    ChannelPostEditedEvent,
    ChannelPostReceivedEvent,
)
from app.models.channel import Channel, ChannelPost, WorkspaceChannel
from app.models.workspace_member import WorkspaceMember
from app.services.channel_history import ingest_snapshots

if TYPE_CHECKING:  # pragma: no cover - typing only
    from aiogram.types import Message

logger = structlog.get_logger(__name__)


PLATFORM_TELEGRAM = "telegram"


# ---------------------------------------------------------------------------
# Result envelope (used by the webhook route for logging / tests)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LiveIngestResult:
    """Outcome of one webhook ingest call.

    The webhook route consumes this only for logging; tests assert
    on the field set to confirm dedup / upsert / drop paths.
    """

    status: str
    """``"inserted"`` / ``"duplicate"`` / ``"edited"`` / ``"edited_insert"``
    / ``"unknown_channel"`` / ``"unknown_message"`` / ``"skipped"``."""

    channel_post_id: uuid.UUID | None = None
    channel_id: uuid.UUID | None = None
    workspace_channel_count: int = 0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(tz=UTC)


async def _resolve_channel(
    session: AsyncSession,
    *,
    external_id: int,
    platform: str = PLATFORM_TELEGRAM,
) -> Channel | None:
    res = await session.execute(
        select(Channel).where(
            Channel.platform == platform,
            Channel.external_id == external_id,
        ),
    )
    return res.scalar_one_or_none()


async def _bindings_for_channel(
    session: AsyncSession,
    channel_id: uuid.UUID,
) -> list[WorkspaceChannel]:
    """Return every *active* binding pointing at ``channel_id``.

    Soft-detached bindings (``disconnected_at IS NOT NULL``) are
    skipped \u2014 the user already removed the channel and shouldn't
    keep receiving updates on it. RLS is bypassed inside the
    webhook route's session by design \u2014 the route is unauthenticated
    and the tenant context is "the channel itself", not a user.
    """

    res = await session.execute(
        select(WorkspaceChannel).where(
            WorkspaceChannel.channel_id == channel_id,
            WorkspaceChannel.disconnected_at.is_(None),
        ),
    )
    return list(res.scalars().all())


async def _members_for_workspace(
    session: AsyncSession,
    workspace_id: uuid.UUID,
) -> list[WorkspaceMember]:
    res = await session.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
        ),
    )
    return list(res.scalars().all())


async def _publish_received(
    redis: Any,
    *,
    binding: WorkspaceChannel,
    channel: Channel,
    row: ChannelPost,
    members: list[WorkspaceMember],
) -> None:
    """Fan out one :class:`ChannelPostReceivedEvent` per workspace member.

    ``ingest_source="webhook"`` distinguishes live ingest from the
    backfill task; downstream consumers (Brand Memory updater) treat
    the two differently \u2014 webhook posts are fresh signal, backfilled
    posts are historical context.
    """

    for member in members:
        await publish_for_user(
            redis,
            member.user_id,
            ChannelPostReceivedEvent(
                workspace_id=str(binding.workspace_id),
                brand_id=str(binding.brand_id),
                user_id=str(member.user_id),
                channel_id=str(channel.id),
                workspace_channel_id=str(binding.id),
                channel_post_id=str(row.id),
                tg_message_id=row.tg_message_id,
                posted_at=row.posted_at,
                has_media=row.has_media,
                ingest_source="webhook",
            ),
        )


async def _publish_edited(
    redis: Any,
    *,
    binding: WorkspaceChannel,
    channel: Channel,
    row: ChannelPost,
    edited_at: datetime,
    members: list[WorkspaceMember],
) -> None:
    for member in members:
        await publish_for_user(
            redis,
            member.user_id,
            ChannelPostEditedEvent(
                workspace_id=str(binding.workspace_id),
                brand_id=str(binding.brand_id),
                user_id=str(member.user_id),
                channel_id=str(channel.id),
                workspace_channel_id=str(binding.id),
                channel_post_id=str(row.id),
                tg_message_id=row.tg_message_id,
                posted_at=row.posted_at,
                edited_at=edited_at,
                has_media=row.has_media,
            ),
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def ingest_live_post(
    session: AsyncSession,
    redis: Any,
    message: Message,
    *,
    edited: bool,
) -> LiveIngestResult:
    """Ingest one webhook ``Message`` (new or edited).

    Flow:

    1. Parse the message into a :class:`ChannelPostSnapshot`. ``None``
       \u2192 service message, drop silently.
    2. Resolve the global ``Channel`` row by
       ``(platform="telegram", external_id=chat.id)``. Unknown
       channel \u2192 drop silently (Telegram will keep delivering;
       silently 204'ing is the documented way to stop the retries).
    3. New post (``edited=False``) \u2192 reuse :func:`ingest_snapshots`
       for the dedup. Inserted row \u2192 publish
       :class:`ChannelPostReceivedEvent` per binding. Duplicate
       \u2192 no event.
    4. Edited post (``edited=True``) \u2192 look up the existing
       ``channel_posts`` row by ``(channel_id, tg_message_id)``.
       Found \u2192 update text / entities / media_summary, publish
       :class:`ChannelPostEditedEvent`. Missing \u2192 fall back to
       the insert path (a TG client may send only the edit if the
       original delivery 5xx'd).

    The caller (webhook route) owns the transaction \u2014 we ``flush``
    so the upsert is observable but the route does ``commit`` /
    ``rollback`` once the dispatcher chain returns.
    """

    # Lazy import keeps the test surface light (the parser pulls in
    # aiogram, which we don't need to import at module load time).
    from app.integrations.telegram.parsers import message_to_snapshot

    snapshot = message_to_snapshot(message)
    if snapshot is None:
        logger.info(
            "channel_ingest.skipped_service_message",
            chat_id=message.chat.id,
            message_id=message.message_id,
        )
        return LiveIngestResult(status="skipped")

    channel = await _resolve_channel(session, external_id=int(message.chat.id))
    if channel is None:
        logger.info(
            "channel_ingest.unknown_channel",
            chat_id=message.chat.id,
            tg_message_id=snapshot.tg_message_id,
        )
        return LiveIngestResult(status="unknown_channel")

    bindings = await _bindings_for_channel(session, channel.id)
    # Pre-gather members per workspace so we don't re-fetch inside
    # the per-binding loop when multiple bindings share a workspace.
    members_by_ws: dict[uuid.UUID, list[WorkspaceMember]] = {}
    for binding in bindings:
        if binding.workspace_id not in members_by_ws:
            members_by_ws[binding.workspace_id] = await _members_for_workspace(
                session,
                binding.workspace_id,
            )

    if edited:
        return await _handle_edited(
            session,
            redis,
            channel=channel,
            snapshot=snapshot,
            message=message,
            bindings=bindings,
            members_by_ws=members_by_ws,
        )

    return await _handle_new(
        session,
        redis,
        channel=channel,
        snapshot=snapshot,
        bindings=bindings,
        members_by_ws=members_by_ws,
    )


async def _handle_new(
    session: AsyncSession,
    redis: Any,
    *,
    channel: Channel,
    snapshot: ChannelPostSnapshot,
    bindings: list[WorkspaceChannel],
    members_by_ws: dict[uuid.UUID, list[WorkspaceMember]],
) -> LiveIngestResult:
    inserted, duplicate_count = await ingest_snapshots(
        session,
        channel_id=channel.id,
        snapshots=[snapshot],
    )
    if not inserted:
        # Duplicate \u2014 ``ingest_snapshots`` already counted it; no
        # event fan-out (the post is already in the DB and was
        # already announced when it first landed).
        logger.info(
            "channel_ingest.duplicate",
            channel_id=str(channel.id),
            tg_message_id=snapshot.tg_message_id,
            duplicate_count=duplicate_count,
        )
        return LiveIngestResult(
            status="duplicate",
            channel_id=channel.id,
            workspace_channel_count=len(bindings),
        )

    row = inserted[0]
    # Refresh ``Channel.last_seen_at`` so the registry reflects the
    # live activity \u2014 the backfill task does the same on each run.
    channel.last_seen_at = _now()
    await session.flush()

    for binding in bindings:
        members = members_by_ws.get(binding.workspace_id, [])
        await _publish_received(
            redis,
            binding=binding,
            channel=channel,
            row=row,
            members=members,
        )

    return LiveIngestResult(
        status="inserted",
        channel_post_id=row.id,
        channel_id=channel.id,
        workspace_channel_count=len(bindings),
    )


async def _handle_edited(
    session: AsyncSession,
    redis: Any,
    *,
    channel: Channel,
    snapshot: ChannelPostSnapshot,
    message: Message,
    bindings: list[WorkspaceChannel],
    members_by_ws: dict[uuid.UUID, list[WorkspaceMember]],
) -> LiveIngestResult:
    res = await session.execute(
        select(ChannelPost).where(
            ChannelPost.channel_id == channel.id,
            ChannelPost.tg_message_id == snapshot.tg_message_id,
        ),
    )
    row = res.scalar_one_or_none()

    edited_at = _now()
    if message.edit_date is not None:
        # ``Message.edit_date`` is the unix timestamp Telegram ships
        # on the Bot API ``edited_channel_post`` payload \u2014 aiogram
        # exposes it as a raw ``int``. Convert to a UTC-aware
        # ``datetime`` so the event schema's ``edited_at`` field is
        # serialised as ISO-8601.
        edited_at = datetime.fromtimestamp(int(message.edit_date), tz=UTC)

    if row is None:
        # Telegram replayed an edit for a post we never saw \u2014 most
        # likely the original ``channel_post`` delivery failed and
        # the bot has been re-subscribed. Fall back to insert so
        # the brand still gets the (latest) version of the row.
        logger.info(
            "channel_ingest.edit_for_unknown_message",
            channel_id=str(channel.id),
            tg_message_id=snapshot.tg_message_id,
        )
        inserted, _duplicate = await ingest_snapshots(
            session,
            channel_id=channel.id,
            snapshots=[snapshot],
        )
        if not inserted:
            # Raced with the original arriving between our SELECT
            # and the INSERT. Fall through to the update path on
            # the row we now know exists.
            res = await session.execute(
                select(ChannelPost).where(
                    ChannelPost.channel_id == channel.id,
                    ChannelPost.tg_message_id == snapshot.tg_message_id,
                ),
            )
            row = res.scalar_one_or_none()
        else:
            row = inserted[0]
            channel.last_seen_at = _now()
            await session.flush()
            for binding in bindings:
                members = members_by_ws.get(binding.workspace_id, [])
                await _publish_received(
                    redis,
                    binding=binding,
                    channel=channel,
                    row=row,
                    members=members,
                )
            return LiveIngestResult(
                status="edited_insert",
                channel_post_id=row.id,
                channel_id=channel.id,
                workspace_channel_count=len(bindings),
            )

    if row is None:  # pragma: no cover - defensive
        return LiveIngestResult(status="unknown_message", channel_id=channel.id)

    # Apply the edit \u2014 text / entities / media_summary / has_media
    # can all change between revisions; ``tg_message_id`` and
    # ``posted_at`` never do.
    row.text = snapshot.text
    row.entities = snapshot.entities
    row.has_media = snapshot.has_media
    row.media_summary = snapshot.media_summary
    channel.last_seen_at = _now()
    await session.flush()

    for binding in bindings:
        members = members_by_ws.get(binding.workspace_id, [])
        await _publish_edited(
            redis,
            binding=binding,
            channel=channel,
            row=row,
            edited_at=edited_at,
            members=members,
        )

    return LiveIngestResult(
        status="edited",
        channel_post_id=row.id,
        channel_id=channel.id,
        workspace_channel_count=len(bindings),
    )


__all__ = [
    "LiveIngestResult",
    "ingest_live_post",
]
