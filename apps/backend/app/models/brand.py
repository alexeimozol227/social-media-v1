"""Brand ORM model.

docs/04-architecture.md §3 — Brand sits below Workspace and above
Channel. Every workspace has at least one brand (created together
with the workspace at sign-up). Content language, tone-of-voice
config, and content_plan / channel posts all hang off the brand.
"""

from __future__ import annotations

import uuid

from sqlalchemy import JSON, Boolean, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin


class Brand(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "brands"
    __table_args__ = (
        # docs/plans/phase1-sprint2-plan.md (PR #14): at most one
        # ``is_default=true`` brand per workspace. Partial unique
        # index supported by both Postgres and SQLite — see
        # migration ``0010_channels_registry`` for the ``WHERE``
        # clause variants on each dialect.
        Index(
            "ux_brands_workspace_default",
            "workspace_id",
            unique=True,
            postgresql_where=text("is_default IS TRUE"),
            sqlite_where=text("is_default = 1"),
        ),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # docs/04-architecture.md §18.2.1 D63: brand-level content language
    # (RU primary). Independent from ``user.locale`` (UI language).
    content_language: Mapped[str] = mapped_column(
        String(16), nullable=False, default="ru", server_default="ru"
    )
    timezone: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="Europe/Minsk",
        server_default="Europe/Minsk",
    )
    # docs/04-architecture.md §20.6 D70 — Level 1 customization:
    # the brand owner can opt out of non-safety skills (skills tagged
    # ``safety`` / ``system`` are filtered at compile-time and cannot
    # be disabled even if their name appears in this array).
    #
    # ``TEXT[]`` on Postgres, ``JSON`` on SQLite (tests).
    disabled_global_skills: Mapped[list[str]] = mapped_column(
        JSON().with_variant(ARRAY(String()), "postgresql"),
        nullable=False,
        default=list,
        server_default="[]",
    )
    # docs/plans/phase1-sprint2-plan.md (PR #14): the brand that
    # ``GET /v1/brands/{id}/channels`` falls through to when no
    # explicit brand id is supplied. Created during sign-up
    # bootstrap (``services.workspaces.ensure_default``).
    is_default: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        default=False,
        server_default="false",
    )

    def __repr__(self) -> str:
        return f"<Brand {self.name} workspace={self.workspace_id}>"


__all__ = ["Brand"]
