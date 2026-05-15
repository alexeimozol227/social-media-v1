"""User ORM model.

docs/04-architecture.md §18.1 + §22 (i18n / multi-currency / multi-tz)
+ docs/06-roadmap.md §5 Сприннт 1 (skeleton).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin


class UserStatus:
    """Constants for the ``users.status`` column.

    Stored as plain ``String(16)`` + CHECK so adding a value in a
    follow-up release is a CHECK swap, not an enum-type ALTER.
    """

    ACTIVE = "active"
    BLOCKED = "blocked"
    DELETED = "deleted"


_STATUS_VALUES = (UserStatus.ACTIVE, UserStatus.BLOCKED, UserStatus.DELETED)


class PlatformRole:
    """Coarse role on the platform itself (not workspace-level).

    docs/04-architecture.md §18.6 D64: this is one of the strict JWT
    claims. RBAC inside a workspace is governed by
    :class:`WorkspaceMemberRole` instead.
    """

    USER = "user"
    SUPPORT = "support"
    MODERATOR = "moderator"
    ADMIN = "admin"


_PLATFORM_ROLES = (
    PlatformRole.USER,
    PlatformRole.SUPPORT,
    PlatformRole.MODERATOR,
    PlatformRole.ADMIN,
)


class User(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'blocked', 'deleted')",
            name="ck_users_status",
        ),
        CheckConstraint(
            "platform_role IN ('user', 'support', 'moderator', 'admin')",
            name="ck_users_platform_role",
        ),
    )

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # i18n / multi-currency / multi-tz defaults from docs/04-architecture.md §18.
    locale: Mapped[str] = mapped_column(
        String(16), nullable=False, default="ru-RU", server_default="ru-RU"
    )
    timezone: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="Europe/Minsk",
        server_default="Europe/Minsk",
    )
    preferred_currency: Mapped[str] = mapped_column(
        String(8), nullable=False, default="RUB", server_default="RUB"
    )

    # Account-status axis. Soft-delete via ``deleted_at`` from the
    # mixin lives orthogonally — a deleted user has ``status='deleted'``
    # AND ``deleted_at`` set.
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=UserStatus.ACTIVE,
        server_default=UserStatus.ACTIVE,
    )

    # docs/04-architecture.md §22: explicit ToS / Privacy acceptance
    # timestamp captured server-side (never read from client).
    tos_accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Email verification timestamp. NULL = unverified. PR #3 will
    # populate this via the email-verification flow.
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    banned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    banned_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    banned_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Coarse platform-level role. JWT claim.
    platform_role: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=PlatformRole.USER,
        server_default=PlatformRole.USER,
    )

    # ``token_version`` is the lever for "sign out everywhere": every
    # access token carries ``tv`` matching this column at issue time;
    # bumping the counter invalidates every outstanding token in one
    # write. Refresh families are revoked in the same transaction so
    # the next ``/v1/auth/refresh`` can't quietly re-mint with the
    # new version.
    token_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    def __repr__(self) -> str:
        return f"<User {self.email}>"


__all__ = ["PlatformRole", "User", "UserStatus"]
