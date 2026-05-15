"""EmailVerification ORM model.

One row per ``(user, purpose, code)`` triple. Adapted from the reference
project (PR-F1). The contract:

* ``code_hash`` is a bcrypt hash; the plaintext 6-digit code is never
  stored. We use bcrypt (not SHA-256 like the long URL-tokens in
  ``refresh_tokens`` / ``password_resets``) because a 6-digit numeric
  code only has ~20 bits of entropy — bcrypt's per-hash cost is what
  blocks an offline brute-force if the row store ever leaks.
* ``consumed_at`` is set when the row is used (success) or
  force-consumed (e.g. after the 5th wrong attempt).
* Application invariant: at most one active row per
  ``(user_id, purpose)`` (see
  :func:`app.services.email_verifications.request_verification`).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

# Reasons we send a verification code. Sign-up confirms ownership of the
# initial email; ``change`` is the future flow when the user updates
# their email from settings (PR-F2 in the reference, follow-up here).
PURPOSE_SIGNUP = "signup"
PURPOSE_CHANGE = "change"

VALID_PURPOSES = frozenset({PURPOSE_SIGNUP, PURPOSE_CHANGE})


class EmailVerification(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "email_verifications"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Target email — equal to ``users.email`` for ``signup``, the new
    # email for ``change``. Stored explicitly so PR-F2 can validate the
    # confirm call before the swap.
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    __table_args__ = (
        CheckConstraint(
            f"purpose IN ('{PURPOSE_SIGNUP}','{PURPOSE_CHANGE}')",
            name="ck_email_verifications_purpose",
        ),
        Index("ix_email_verifications_user_purpose", "user_id", "purpose"),
    )

    def __repr__(self) -> str:
        return f"<EmailVerification user_id={self.user_id} purpose={self.purpose}>"


__all__ = [
    "PURPOSE_CHANGE",
    "PURPOSE_SIGNUP",
    "VALID_PURPOSES",
    "EmailVerification",
]
