"""Telegram user-bot session ORM model (PR #18).

docs/05-tech-stack.md §5.2 + D40: platform-level pool of MTProto
user-bot accounts. Each row stores encrypted ``api_id``, ``api_hash``
and Pyrogram session string. The pool rotator
(:mod:`app.adapters.userbot.pool`) picks the oldest active session
with ``SKIP LOCKED`` so concurrent workers don't collide.

The table is **NOT tenant-scoped** — it lives outside the RLS
boundary (like ``channels``). One user-bot reads public channels on
behalf of every workspace; the Global Channel Registry deduplicates
channel metadata across tenants.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

UserbotSessionStatus = Literal["active", "disabled", "flood_wait", "banned"]


class TelegramUserbotSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One MTProto user-bot account in the platform rotation pool.

    All credential fields (``api_id_encrypted``, ``api_hash_encrypted``,
    ``session_encrypted``) are Fernet blobs — the plaintext is only
    materialised in memory by :func:`app.services.userbot_sessions.decrypt_session`.
    """

    __tablename__ = "telegram_userbot_sessions"

    phone_number: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        unique=True,
    )
    account_label: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    api_id_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    api_hash_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    session_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="active",
    )

    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_healthcheck_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_healthcheck_ok: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
    )
    flood_wait_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    usage_count_24h: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'disabled', 'flood_wait', 'banned')",
            name="ck_telegram_userbot_sessions_status",
        ),
        Index(
            "ix_telegram_userbot_sessions_rotation",
            "status",
            "last_used_at",
        ),
        Index(
            "ix_telegram_userbot_sessions_active",
            "status",
            postgresql_where="status = 'active'",
        ),
    )


__all__ = [
    "TelegramUserbotSession",
    "UserbotSessionStatus",
]
