"""User-bot session pool with oldest-active rotation (PR #18).

docs/04-architecture.md §6.2 (distributed parser): when multiple
worker processes share a user-bot pool, each one MUST pick a
different session — otherwise a single Telegram account would absorb
every parallel request and trip FloodWait. The classic trick on
Postgres is ``SELECT ... FOR UPDATE SKIP LOCKED`` over the rotation
index; SQLite falls back to a plain SELECT (single-worker tests
don't have the contention to worry about).

Rotation policy
---------------
1. ``status = 'active'``.
2. ``flood_wait_until IS NULL OR flood_wait_until <= now()`` — skip
   sessions still in cooldown.
3. ORDER BY ``last_used_at`` NULLS FIRST — never-used sessions get
   first pick, then the longest-unused session.
4. ``LIMIT 1 FOR UPDATE SKIP LOCKED`` on Postgres.
5. ``mark_used`` bumps ``last_used_at`` + ``usage_count_24h`` in the
   same transaction so concurrent workers see the change after the
   row lock releases.

The lifecycle helpers (``mark_flood_wait`` / ``mark_banned`` /
``mark_healthcheck``) write a single column each and commit the
caller's session — the Celery task / service layer drives the
transaction boundary.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.userbot.base import UserBotClient
from app.core.config import settings
from app.models.telegram_userbot_session import TelegramUserbotSession

logger = structlog.get_logger(__name__)


class UserBotPoolEmptyError(RuntimeError):
    """Raised when :meth:`UserBotPool.pick_next` finds no eligible row."""


ClientFactory = Callable[[TelegramUserbotSession], UserBotClient]


@dataclass
class UserBotPool:
    """In-process rotator over :class:`TelegramUserbotSession` rows.

    Construction takes a ``client_factory`` callable so the pool
    decouples row selection from client instantiation: production
    decrypts credentials and builds a :class:`PyrogramUserBotClient`;
    tests pass a closure that returns a :class:`MockUserBotClient`.
    """

    client_factory: ClientFactory

    async def pick_next(
        self,
        session: AsyncSession,
    ) -> tuple[TelegramUserbotSession, UserBotClient]:
        """Lock and return the next eligible session + bound client.

        The caller owns the transaction — pick a session inside the
        same ``async with session.begin()`` block as the subsequent
        :meth:`mark_used` / :meth:`mark_flood_wait` call so the row
        lock spans the whole user-bot interaction.

        Raises :class:`UserBotPoolEmptyError` when no active /
        non-cooldown row is available.
        """

        from sqlalchemy import or_

        now = datetime.now(tz=UTC)
        stmt = (
            select(TelegramUserbotSession)
            .where(
                TelegramUserbotSession.status == "active",
                or_(
                    TelegramUserbotSession.flood_wait_until.is_(None),
                    TelegramUserbotSession.flood_wait_until <= now,
                ),
            )
            .order_by(
                TelegramUserbotSession.last_used_at.asc().nulls_first(),
            )
            .limit(1)
        )

        bind = session.get_bind()
        dialect_name = bind.dialect.name if bind is not None else ""
        if dialect_name == "postgresql":
            stmt = stmt.with_for_update(skip_locked=True)

        result = await session.execute(stmt)
        candidates = result.scalars().all()
        row: TelegramUserbotSession | None = None
        for candidate in candidates:
            # Defense-in-depth: re-check cooldown for SQLite (timezone
            # round-tripping can produce naive comparisons).
            if candidate.flood_wait_until is None or _aware(candidate.flood_wait_until) <= now:
                row = candidate
                break

        if row is None:
            # On Postgres ``SKIP LOCKED`` returned 0 rows; everything
            # else was either non-active, in cooldown, or being
            # used by another worker. The service layer maps this
            # to ``USERBOT_NO_AVAILABLE_SESSION``.
            raise UserBotPoolEmptyError("No active user-bot sessions available")

        client = self.client_factory(row)
        return row, client

    async def mark_used(
        self,
        row: TelegramUserbotSession,
        session: AsyncSession,
    ) -> None:
        """Bump ``last_used_at`` + ``usage_count_24h``.

        Called right after a successful user-bot call so the rotation
        query picks a different session next time.
        """

        row.last_used_at = datetime.now(tz=UTC)
        row.usage_count_24h = (row.usage_count_24h or 0) + 1
        await session.flush()

    async def mark_flood_wait(
        self,
        row: TelegramUserbotSession,
        retry_after: int,
        session: AsyncSession,
    ) -> None:
        """Mark ``row`` as cooling down for ``retry_after`` seconds.

        The cooldown timestamp is clamped to at least
        ``settings.userbot_flood_wait_cooldown_minutes`` because
        Telegram's reported ``retry_after`` is sometimes optimistic.
        """

        cooldown_floor = timedelta(minutes=settings.userbot_flood_wait_cooldown_minutes)
        cooldown = max(timedelta(seconds=int(retry_after)), cooldown_floor)
        row.status = "flood_wait"
        row.flood_wait_until = datetime.now(tz=UTC) + cooldown
        await session.flush()
        logger.info(
            "userbot.mark_flood_wait",
            session_id=str(row.id),
            account_label=row.account_label,
            cooldown_seconds=int(cooldown.total_seconds()),
        )

    async def mark_banned(
        self,
        row: TelegramUserbotSession,
        session: AsyncSession,
    ) -> None:
        """Mark ``row`` as permanently banned. Triggered by
        :class:`UserBotAuthError` from the underlying client."""

        row.status = "banned"
        await session.flush()
        logger.warning(
            "userbot.mark_banned",
            session_id=str(row.id),
            account_label=row.account_label,
        )

    async def mark_healthcheck(
        self,
        row: TelegramUserbotSession,
        ok: bool,
        session: AsyncSession,
    ) -> None:
        """Record the outcome of :meth:`UserBotClient.healthcheck`."""

        row.last_healthcheck_at = datetime.now(tz=UTC)
        row.last_healthcheck_ok = ok
        await session.flush()


def _aware(value: datetime) -> datetime:
    """Normalise a possibly-naive timestamp to UTC.

    SQLite returns naive datetimes even when the column is declared
    ``timezone=True``; coerce so comparisons in :meth:`pick_next`
    don't blow up.
    """

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


__all__ = [
    "ClientFactory",
    "UserBotPool",
    "UserBotPoolEmptyError",
]
