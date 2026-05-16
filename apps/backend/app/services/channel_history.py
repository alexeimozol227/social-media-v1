"""Channel history backfill business logic (PR #15).

docs/plans/phase1-sprint2-plan.md §"Бэкенд — Celery / history
backfill":

* :func:`ingest_snapshots` — write a batch of
  :class:`app.adapters.social.ChannelPostSnapshot` rows into
  ``channel_posts`` with idempotent dedup on
  ``(channel_id, tg_message_id)``.
* :func:`run_backfill` — orchestrates one backfill run for an
  active binding: refresh subscribers count, call the adapter,
  ingest snapshots, publish per-post events + a completion event,
  write the audit row.

Module is adapter-agnostic — it speaks to
:class:`app.adapters.social.TelegramBotClient` and the per-user
event bus. Tests inject ``MockTelegramBotClient`` + ``fakeredis``
to exercise both happy-path and dedup branches without touching
Telegram or a real Redis.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.social import (
    ChannelPostSnapshot,
    TelegramBotClient,
    TelegramChannelNotFoundError,
    TelegramTransportError,
)
from app.core.event_bus import publish_for_user
from app.errors import (
    ChannelBackfillNotConfiguredError,
    ChannelNotConnectedError,
    ChannelNotFoundError,
    TelegramAPIError,
)
from app.events.schemas import (
    ChannelBackfillCompletedEvent,
    ChannelPostReceivedEvent,
)
from app.models.channel import (
    Channel,
    ChannelPost,
    WorkspaceChannel,
)

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Public dataclasses (service-level result envelope)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BackfillSummary:
    """Result of one backfill run, returned by :func:`run_backfill`.

    Mirrors the payload of :class:`ChannelBackfillCompletedEvent`
    so the Celery task can publish the event without re-deriving
    the counts.
    """

    workspace_channel_id: uuid.UUID
    channel_id: uuid.UUID
    fetched_count: int
    inserted_count: int
    duplicate_count: int
    status: str
    """``"ok"`` / ``"no_history"`` / ``"adapter_unsupported"`` /
    ``"transport_error"`` / ``"not_connected"`` / ``"not_found"``.
    Mirrors :class:`ChannelBackfillCompletedEvent.status`."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _snapshot_to_columns(snapshot: ChannelPostSnapshot) -> dict[str, Any]:
    """Project a :class:`ChannelPostSnapshot` onto the ORM column dict.

    Kept out-of-line so the dedup-retry branch in
    :func:`ingest_snapshots` doesn't duplicate the mapping.
    """

    return {
        "tg_message_id": snapshot.tg_message_id,
        "text": snapshot.text,
        "entities": snapshot.entities,
        "has_media": snapshot.has_media,
        "media_summary": snapshot.media_summary,
        "views_count": snapshot.views_count,
        "reactions_count": snapshot.reactions_count,
        "forwards_count": snapshot.forwards_count,
        "posted_at": snapshot.posted_at,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def ingest_snapshots(
    session: AsyncSession,
    *,
    channel_id: uuid.UUID,
    snapshots: list[ChannelPostSnapshot],
) -> tuple[list[ChannelPost], int]:
    """Insert ``snapshots`` into ``channel_posts``, deduped per row.

    Strategy: pre-filter against existing ``tg_message_id`` values
    in a single SELECT (no N+1 round-trip), then attempt INSERT for
    the remaining snapshots. The unique constraint on
    ``(channel_id, tg_message_id)`` is the source-of-truth — a
    concurrent ingest racing us will trip the constraint, which we
    swallow per-row and count as a duplicate.

    Returns ``(inserted_rows, duplicate_count)``. ``inserted_rows``
    is ordered by ``tg_message_id`` ascending (chronological) so
    downstream event publishing stays deterministic across reruns.
    """

    if not snapshots:
        return [], 0

    incoming_ids = {s.tg_message_id for s in snapshots}
    existing_q = select(ChannelPost.tg_message_id).where(
        ChannelPost.channel_id == channel_id,
        ChannelPost.tg_message_id.in_(incoming_ids),
    )
    res = await session.execute(existing_q)
    existing_ids = {row[0] for row in res.all()}

    inserted: list[ChannelPost] = []
    duplicate_count = len(snapshots) - len(incoming_ids - existing_ids)
    # ``duplicate_count`` so far covers the pre-filter hits; raced
    # inserts (constraint violation below) bump it further.

    # Sort ascending so audit trails / events appear in chronological
    # order; the adapter returns newest-first but downstream
    # consumers expect publication order.
    chronological = sorted(snapshots, key=lambda s: s.tg_message_id)

    for snapshot in chronological:
        if snapshot.tg_message_id in existing_ids:
            continue
        row = ChannelPost(
            channel_id=channel_id,
            **_snapshot_to_columns(snapshot),
        )
        session.add(row)
        try:
            # Per-row flush so a single IntegrityError doesn't
            # poison the whole batch — the surrounding transaction
            # is the route handler's, so we can't roll the savepoint
            # back here without nesting. SQLAlchemy auto-opens a
            # SAVEPOINT for the flush in this case via the
            # ``begin_nested`` context manager.
            async with session.begin_nested():
                await session.flush()
        except IntegrityError:
            duplicate_count += 1
            session.expunge(row)
            continue
        inserted.append(row)

    return inserted, duplicate_count


async def _resolve_active_binding(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    brand_id: uuid.UUID,
    workspace_channel_id: uuid.UUID,
) -> tuple[WorkspaceChannel, Channel]:
    """Fetch the active binding + its global Channel row.

    Raises typed errors for the three failure modes the API and
    Celery task share:

    * :class:`ChannelNotFoundError` — no binding for this brand.
    * :class:`ChannelNotConnectedError` — soft-detached binding.
    """

    res = await session.execute(
        select(WorkspaceChannel, Channel)
        .join(Channel, Channel.id == WorkspaceChannel.channel_id)
        .where(
            WorkspaceChannel.id == workspace_channel_id,
            WorkspaceChannel.workspace_id == workspace_id,
            WorkspaceChannel.brand_id == brand_id,
        ),
    )
    row = res.first()
    if row is None:
        raise ChannelNotFoundError()
    binding, channel = row[0], row[1]
    if binding.disconnected_at is not None:
        raise ChannelNotConnectedError()
    return binding, channel


async def _refresh_subscribers_count(
    session: AsyncSession,
    client: TelegramBotClient,
    channel: Channel,
) -> None:
    """Update ``Channel.subscribers_count`` + ``last_seen_at``.

    Best-effort — a transient Bot API blip MUST NOT abort the
    backfill. We log the failure and leave the previous count in
    place; the next backfill will retry.
    """

    try:
        count = await client.get_chat_member_count(channel.external_id)
    except (TelegramChannelNotFoundError, TelegramTransportError) as exc:
        logger.warning(
            "channel_history.refresh_subscribers_failed",
            channel_id=str(channel.id),
            error=exc.__class__.__name__,
        )
        return
    channel.subscribers_count = count
    channel.last_seen_at = _now()
    await session.flush()


async def run_backfill(
    session: AsyncSession,
    redis: Any,
    client: TelegramBotClient,
    *,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID,
    brand_id: uuid.UUID,
    workspace_channel_id: uuid.UUID,
    limit: int,
    task_id: str,
    from_message_id: int | None = None,
) -> BackfillSummary:
    """Run one history-backfill pass for an active binding.

    Idempotent end-to-end: re-running with the same ``limit`` and
    ``from_message_id`` returns ``inserted_count = 0`` and
    ``duplicate_count == fetched_count``. The route handler /
    Celery task owns the transaction; this function only flushes
    so the caller can audit + commit atomically.
    """

    binding, channel = await _resolve_active_binding(
        session,
        workspace_id=workspace_id,
        brand_id=brand_id,
        workspace_channel_id=workspace_channel_id,
    )

    await _refresh_subscribers_count(session, client, channel)

    try:
        snapshots = await client.fetch_channel_history(
            channel.external_id,
            limit=limit,
            from_message_id=from_message_id,
        )
    except TelegramChannelNotFoundError as exc:
        logger.warning(
            "channel_history.fetch_not_found",
            channel_id=str(channel.id),
            external_id=channel.external_id,
        )
        raise ChannelNotFoundError() from exc
    except TelegramTransportError as exc:
        logger.warning(
            "channel_history.fetch_transport_error",
            channel_id=str(channel.id),
            external_id=channel.external_id,
        )
        raise TelegramAPIError() from exc

    fetched = len(snapshots)
    if fetched == 0:
        status = "no_history"
        inserted_rows: list[ChannelPost] = []
        duplicate_count = 0
    else:
        inserted_rows, duplicate_count = await ingest_snapshots(
            session,
            channel_id=channel.id,
            snapshots=snapshots,
        )
        status = "ok"

    # Publish a per-post event for every newly persisted row so the
    # Brand Memory pipeline (Sprint 3) and the embeddings job
    # (PR #17) wake up automatically. ``best-effort`` semantics:
    # the publish failure path inside ``publish_for_user`` already
    # swallows + logs; we don't second-guess it here.
    for row in inserted_rows:
        await publish_for_user(
            redis,
            user_id,
            ChannelPostReceivedEvent(
                workspace_id=str(workspace_id),
                brand_id=str(brand_id),
                user_id=str(user_id),
                channel_id=str(channel.id),
                workspace_channel_id=str(binding.id),
                channel_post_id=str(row.id),
                tg_message_id=row.tg_message_id,
                posted_at=row.posted_at,
                has_media=row.has_media,
                ingest_source="backfill",
            ),
        )

    await publish_for_user(
        redis,
        user_id,
        ChannelBackfillCompletedEvent(
            workspace_id=str(workspace_id),
            brand_id=str(brand_id),
            user_id=str(user_id),
            channel_id=str(channel.id),
            workspace_channel_id=str(binding.id),
            task_id=task_id,
            status=status,
            fetched_count=fetched,
            inserted_count=len(inserted_rows),
            duplicate_count=duplicate_count,
        ),
    )

    return BackfillSummary(
        workspace_channel_id=binding.id,
        channel_id=channel.id,
        fetched_count=fetched,
        inserted_count=len(inserted_rows),
        duplicate_count=duplicate_count,
        status=status,
    )


__all__ = [
    "BackfillSummary",
    "ChannelBackfillNotConfiguredError",
    "ingest_snapshots",
    "run_backfill",
]
