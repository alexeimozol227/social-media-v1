"""Tests for the :class:`TelegramUserbotSession` ORM model + 0012 migration shape."""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.telegram_userbot_session import TelegramUserbotSession


@pytest.mark.asyncio
async def test_table_shape(db_session: AsyncSession) -> None:
    """Every required column is present with the documented type."""

    inspector = sa.inspect(TelegramUserbotSession).local_table
    cols = {c.name: c for c in inspector.columns}
    assert "phone_number" in cols
    assert "account_label" in cols
    assert "api_id_encrypted" in cols
    assert "api_hash_encrypted" in cols
    assert "session_encrypted" in cols
    assert "status" in cols
    assert "last_used_at" in cols
    assert "last_healthcheck_at" in cols
    assert "last_healthcheck_ok" in cols
    assert "flood_wait_until" in cols
    assert "usage_count_24h" in cols
    assert "notes" in cols
    assert isinstance(cols["api_id_encrypted"].type, sa.LargeBinary)
    assert isinstance(cols["api_hash_encrypted"].type, sa.LargeBinary)
    assert isinstance(cols["session_encrypted"].type, sa.LargeBinary)


@pytest.mark.asyncio
async def test_status_check_constraint(db_session: AsyncSession) -> None:
    """``status`` must be one of the four allowed values."""

    row = TelegramUserbotSession(
        phone_number="+15550000001",
        account_label="bot-1",
        api_id_encrypted=b"e",
        api_hash_encrypted=b"e",
        session_encrypted=b"e",
        status="zombie",  # not in the allowed set
        usage_count_24h=0,
    )
    db_session.add(row)
    with pytest.raises(sa.exc.IntegrityError):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_phone_number_unique(db_session: AsyncSession) -> None:
    """Two sessions with the same phone are rejected by the unique constraint."""

    row_a = TelegramUserbotSession(
        phone_number="+15550000777",
        account_label="bot-a",
        api_id_encrypted=b"a",
        api_hash_encrypted=b"a",
        session_encrypted=b"a",
        status="active",
        usage_count_24h=0,
    )
    db_session.add(row_a)
    await db_session.flush()
    row_b = TelegramUserbotSession(
        phone_number="+15550000777",
        account_label="bot-b",
        api_id_encrypted=b"b",
        api_hash_encrypted=b"b",
        session_encrypted=b"b",
        status="active",
        usage_count_24h=0,
    )
    db_session.add(row_b)
    with pytest.raises(sa.exc.IntegrityError):
        await db_session.flush()
    await db_session.rollback()
