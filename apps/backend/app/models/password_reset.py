"""PasswordReset ORM model (PR-T8 in the reference).

Stores hashed one-shot tokens for the ``forgot-password`` flow. The
plaintext token is what we drop into the email link
(``/reset-password?token=...``). It is **never** stored — only the
SHA-256 hex digest. Look-up is by hash.

Lifecycle:

* **Active** — ``consumed_at IS NULL AND expires_at > now()``.
  Presenting an active token consumes it (see
  :func:`app.services.password_reset.consume_reset`).
* **Expired** — ``expires_at <= now()``. Distinct from "consumed" so
  the UI can render "this link has expired, request a new one"
  instead of the generic "invalid token".
* **Consumed** — ``consumed_at IS NOT NULL``. The row stays around
  for audit; presenting the same token again is rejected.

Token entropy is ``secrets.token_urlsafe(32)`` → ~256 bits, so a fast
SHA-256 is fine. Bcrypt would dominate the verify-time budget without
buying any security here.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PasswordReset(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "password_resets"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    ip_requested: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)


__all__ = ["PasswordReset"]
