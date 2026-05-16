"""Tests for :class:`app.adapters.userbot.UserBotPool` rotation logic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.userbot import (
    MockUserBotClient,
    UserBotPool,
    UserBotPoolEmptyError,
)
from app.models.telegram_userbot_session import TelegramUserbotSession


async def _seed(
    session: AsyncSession,
    *,
    phone: str,
    label: str,
    status: str = "active",
    last_used_at: datetime | None = None,
    flood_wait_until: datetime | None = None,
) -> TelegramUserbotSession:
    row = TelegramUserbotSession(
        phone_number=phone,
        account_label=label,
        api_id_encrypted=b"e",
        api_hash_encrypted=b"e",
        session_encrypted=b"e",
        status=status,
        last_used_at=last_used_at,
        flood_wait_until=flood_wait_until,
        usage_count_24h=0,
    )
    session.add(row)
    await session.flush()
    return row


def _build_pool() -> UserBotPool:
    return UserBotPool(client_factory=lambda _row: MockUserBotClient())


@pytest.mark.asyncio
async def test_pick_next_returns_oldest_active(db_session: AsyncSession) -> None:
    now = datetime.now(tz=UTC)
    await _seed(
        db_session,
        phone="+1",
        label="recent",
        last_used_at=now - timedelta(minutes=1),
    )
    older = await _seed(
        db_session,
        phone="+2",
        label="older",
        last_used_at=now - timedelta(hours=1),
    )
    never_used = await _seed(
        db_session,
        phone="+3",
        label="never",
        last_used_at=None,
    )

    pool = _build_pool()
    row, client = await pool.pick_next(db_session)
    # Never-used wins over older.
    assert row.id == never_used.id
    assert isinstance(client, MockUserBotClient)
    _ = older  # silence unused


@pytest.mark.asyncio
async def test_pick_next_skips_flood_wait_until_cooldown(db_session: AsyncSession) -> None:
    now = datetime.now(tz=UTC)
    await _seed(
        db_session,
        phone="+1",
        label="cooling",
        flood_wait_until=now + timedelta(minutes=10),
    )
    fresh = await _seed(db_session, phone="+2", label="fresh")

    pool = _build_pool()
    row, _client = await pool.pick_next(db_session)
    assert row.id == fresh.id


@pytest.mark.asyncio
async def test_pick_next_skips_disabled_and_banned(db_session: AsyncSession) -> None:
    await _seed(db_session, phone="+1", label="banned", status="banned")
    await _seed(db_session, phone="+2", label="disabled", status="disabled")
    fresh = await _seed(db_session, phone="+3", label="fresh")

    pool = _build_pool()
    row, _client = await pool.pick_next(db_session)
    assert row.id == fresh.id


@pytest.mark.asyncio
async def test_pick_next_empty_pool_raises(db_session: AsyncSession) -> None:
    await _seed(db_session, phone="+1", label="banned", status="banned")
    pool = _build_pool()
    with pytest.raises(UserBotPoolEmptyError):
        await pool.pick_next(db_session)


@pytest.mark.asyncio
async def test_mark_used_bumps_last_used_at_and_usage_count(
    db_session: AsyncSession,
) -> None:
    row = await _seed(db_session, phone="+1", label="bot")
    pool = _build_pool()
    before = row.last_used_at
    await pool.mark_used(row, db_session)
    assert row.last_used_at is not None
    assert before != row.last_used_at
    assert row.usage_count_24h == 1
    await pool.mark_used(row, db_session)
    assert row.usage_count_24h == 2


@pytest.mark.asyncio
async def test_mark_flood_wait_sets_status_and_cooldown(
    db_session: AsyncSession,
) -> None:
    row = await _seed(db_session, phone="+1", label="bot")
    pool = _build_pool()
    await pool.mark_flood_wait(row, retry_after=30, session=db_session)
    assert row.status == "flood_wait"
    assert row.flood_wait_until is not None
    # Cooldown is floored to settings.userbot_flood_wait_cooldown_minutes (60 by default),
    # so 30s ``retry_after`` still maps to >= 60 minutes ahead.
    cooldown_seconds = (
        row.flood_wait_until - datetime.now(tz=UTC).replace(tzinfo=row.flood_wait_until.tzinfo)
    ).total_seconds()
    assert cooldown_seconds > 60 * 30  # at least 30 minutes


@pytest.mark.asyncio
async def test_mark_banned_flips_status(db_session: AsyncSession) -> None:
    row = await _seed(db_session, phone="+1", label="bot")
    pool = _build_pool()
    await pool.mark_banned(row, db_session)
    assert row.status == "banned"


@pytest.mark.asyncio
async def test_mark_healthcheck_records_outcome(db_session: AsyncSession) -> None:
    row = await _seed(db_session, phone="+1", label="bot")
    pool = _build_pool()
    await pool.mark_healthcheck(row, ok=True, session=db_session)
    assert row.last_healthcheck_ok is True
    assert row.last_healthcheck_at is not None
    await pool.mark_healthcheck(row, ok=False, session=db_session)
    assert row.last_healthcheck_ok is False
