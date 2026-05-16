"""Channel connect / list / detach / verify business logic (PR #14).

docs/plans/phase1-sprint2-plan.md §"Бэкенд — сервисный слой":

* :func:`connect` — admin-rights check via the Bot adapter → upsert
  into the Global Channel Registry → bridge row in
  ``workspace_channels`` → audit + event-bus + commit.
* :func:`list_for_brand` — read-side projection of the brand's
  active (i.e. ``disconnected_at IS NULL``) channels for the
  dashboard table.
* :func:`detach` — soft detach; flips ``disconnected_at`` so the
  audit trail / history stay intact.
* :func:`verify` — re-runs the admin check against the live Bot
  API and refreshes the ``bot_admin_rights`` snapshot.

The module is intentionally adapter-agnostic — it speaks to
:class:`app.adapters.social.TelegramBotClient`, never to aiogram
directly. Unit tests inject ``MockTelegramBotClient``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.social import (
    ChannelInfo,
    ChatMemberInfo,
    TelegramBotClient,
    TelegramChannelNotFoundError,
    TelegramTransportError,
)
from app.errors import (
    BotMissingPostPermissionError,
    BotNotAdminError,
    ChannelAlreadyConnectedError,
    ChannelNotConnectedError,
    ChannelNotFoundError,
    TelegramAPIError,
)
from app.models.channel import (
    Channel,
    WorkspaceChannel,
    WorkspaceChannelRoleValues,
)

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _admin_rights_snapshot(member: ChatMemberInfo) -> dict[str, Any]:
    """Render a ``ChatMemberInfo`` into the JSON we persist on
    ``workspace_channels.bot_admin_rights``.

    Status is always ``administrator`` or ``creator`` by the time
    we reach the snapshot — we re-check upstream — but we keep the
    raw status in the JSON so the admin lens can spot a member who
    silently lost admin rights between calls. ``creator`` has every
    right implicitly per the Bot API; we coerce the flags so the
    snapshot matches the SPA's "can post" check regardless of which
    adapter (aiogram vs mock) populated the source object.
    """

    if member.status == "creator":
        can_post = True
        can_edit = True
        can_delete = True
    else:
        can_post = member.can_post_messages
        can_edit = member.can_edit_messages
        can_delete = member.can_delete_messages
    return {
        "status": member.status,
        "can_post_messages": can_post,
        "can_edit_messages": can_edit,
        "can_delete_messages": can_delete,
        "captured_at": _now().isoformat(),
    }


async def _resolve_channel_info(
    client: TelegramBotClient,
    identifier: str | int,
) -> ChannelInfo:
    try:
        return await client.get_chat(identifier)
    except TelegramChannelNotFoundError as exc:
        raise ChannelNotFoundError() from exc
    except TelegramTransportError as exc:
        raise TelegramAPIError() from exc


async def _verify_bot_admin(
    client: TelegramBotClient,
    chat_id: int,
) -> ChatMemberInfo:
    """Confirm the bot is an admin with ``can_post_messages``.

    Raises:
        :class:`BotNotAdminError` — the bot isn't in the channel or
          isn't an admin.
        :class:`BotMissingPostPermissionError` — the bot is admin
          but lacks ``can_post_messages``.
        :class:`TelegramAPIError` — transport failure.
    """

    try:
        me_id = await client.get_me_id()
        member = await client.get_chat_member(chat_id, me_id)
    except TelegramChannelNotFoundError as exc:
        raise BotNotAdminError() from exc
    except TelegramTransportError as exc:
        raise TelegramAPIError() from exc

    if member.status not in {"administrator", "creator"}:
        raise BotNotAdminError()
    # ``creator`` has every right implicitly; ``administrator``
    # needs the explicit flag.
    if member.status == "administrator" and not member.can_post_messages:
        raise BotMissingPostPermissionError()
    return member


async def _upsert_registry(
    session: AsyncSession,
    info: ChannelInfo,
    *,
    platform: str = "telegram",
) -> Channel:
    """Idempotent upsert by ``(platform, external_id)``.

    First call ever for a channel → insert. Repeat connects from
    another workspace → update the cached title / username /
    description / ``last_seen_at`` so the registry stays fresh.
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
        # Concurrent connect from another request raced us to the
        # insert; re-fetch and return the winner.
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


async def connect(
    session: AsyncSession,
    client: TelegramBotClient,
    *,
    workspace_id: uuid.UUID,
    brand_id: uuid.UUID,
    identifier: str | int,
    platform: str = "telegram",
) -> WorkspaceChannel:
    """Resolve, verify, register and bind a channel to ``brand_id``.

    Order of operations matters:

    1. ``get_chat`` — surfaces a typed 404 before we touch the DB.
    2. ``get_chat_member`` for the bot itself — surfaces a typed
       403 before we touch the DB.
    3. Upsert into the registry.
    4. Insert the ``workspace_channels`` row; conflict → typed 409.

    The caller (route handler) is responsible for ``await db.commit()``
    after this returns so the audit / event-bus writes can join the
    same transaction.
    """

    info = await _resolve_channel_info(client, identifier)
    member = await _verify_bot_admin(client, info.chat_id)

    channel = await _upsert_registry(session, info, platform=platform)

    # Idempotency / 409: a channel already bound (and still
    # connected) to this brand from a previous successful call.
    res = await session.execute(
        select(WorkspaceChannel).where(
            WorkspaceChannel.workspace_id == workspace_id,
            WorkspaceChannel.brand_id == brand_id,
            WorkspaceChannel.channel_id == channel.id,
        ),
    )
    existing = res.scalar_one_or_none()
    if existing is not None and existing.disconnected_at is None:
        raise ChannelAlreadyConnectedError()
    if existing is not None and existing.disconnected_at is not None:
        # Reconnect: lift the soft-detach and refresh the snapshot.
        existing.disconnected_at = None
        existing.connected_at = _now()
        existing.bot_admin_rights = _admin_rights_snapshot(member)
        await session.flush()
        return existing

    binding = WorkspaceChannel(
        workspace_id=workspace_id,
        brand_id=brand_id,
        channel_id=channel.id,
        role=WorkspaceChannelRoleValues.OWNED,
        bot_admin_rights=_admin_rights_snapshot(member),
    )
    session.add(binding)
    try:
        await session.flush()
    except IntegrityError as exc:
        # Concurrent connect race; map to typed 409.
        await session.rollback()
        raise ChannelAlreadyConnectedError() from exc
    return binding


async def list_for_brand(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    brand_id: uuid.UUID,
    include_disconnected: bool = False,
) -> tuple[list[tuple[WorkspaceChannel, Channel]], int]:
    """Return the brand's channels + total count.

    The route layer formats the result as :class:`ChannelView` —
    keeping the projection here would couple the service to the
    API schema.
    """

    base = (
        select(WorkspaceChannel, Channel)
        .join(Channel, Channel.id == WorkspaceChannel.channel_id)
        .where(
            WorkspaceChannel.workspace_id == workspace_id,
            WorkspaceChannel.brand_id == brand_id,
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
    )
    if not include_disconnected:
        count_q = count_q.where(WorkspaceChannel.disconnected_at.is_(None))
    total = (await session.execute(count_q)).scalar_one()
    return rows, int(total or 0)


async def get_binding(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    brand_id: uuid.UUID,
    workspace_channel_id: uuid.UUID,
) -> tuple[WorkspaceChannel, Channel] | None:
    """Look up an active binding by its row id."""

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
        return None
    return row[0], row[1]


async def detach(
    session: AsyncSession,
    binding: WorkspaceChannel,
) -> WorkspaceChannel:
    """Soft-detach an active binding. No-op on an already-detached row.

    Raises :class:`ChannelNotConnectedError` so the route layer can
    map "this row was already detached" to a typed 409 instead of
    a 204.
    """

    if binding.disconnected_at is not None:
        raise ChannelNotConnectedError()
    binding.disconnected_at = _now()
    await session.flush()
    return binding


async def verify(
    session: AsyncSession,
    client: TelegramBotClient,
    binding: WorkspaceChannel,
    channel: Channel,
) -> WorkspaceChannel:
    """Re-run the admin-rights probe and refresh the snapshot.

    Used by ``POST /v1/brands/{id}/channels/{cid}/verify`` so the
    UI can tell the user "your bot lost ``can_post_messages``,
    please re-promote it" without waiting for the next publish
    attempt to fail.
    """

    if binding.disconnected_at is not None:
        raise ChannelNotConnectedError()
    member = await _verify_bot_admin(client, channel.external_id)
    binding.bot_admin_rights = _admin_rights_snapshot(member)
    await session.flush()
    return binding


__all__ = [
    "connect",
    "detach",
    "get_binding",
    "list_for_brand",
    "verify",
]
