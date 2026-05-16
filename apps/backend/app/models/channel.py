"""Channel + WorkspaceChannel + ChannelPost ORM models.

docs/04-architecture.md §11.3 + docs/03 §D20 + docs/plans/
phase1-sprint2-plan.md (PR #14):

* :class:`Channel` — Global Channel Registry. One row per
  ``(platform, external_id)`` shared across every workspace that
  ever connects the same channel. Not tenant-scoped — RLS lives
  on :class:`WorkspaceChannel`.
* :class:`WorkspaceChannel` — bridge row binding a channel to a
  brand inside a workspace. Carries the role discriminator
  (``owned`` vs ``competitor``) and a snapshot of the bot's admin
  rights so the admin lens can spot a bot that lost
  ``can_post_messages`` without an extra Bot API round-trip.
* :class:`ChannelPost` — every post we observe on a channel. The
  Postgres table is monthly-partitioned via pg_partman; the ORM
  doesn't care, lookups are always by ``id`` (or ``(channel_id,
  tg_message_id)`` for the ingest dedup path).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

# Closed vocabularies — kept as Literals + module-level tuples so
# adding a value later is a one-line code change. Mirrors the
# ``audit_event.Severity`` pattern.
ChannelPlatform = Literal["telegram"]
WorkspaceChannelRole = Literal["owned", "competitor"]


class ChannelPlatformValues:
    TELEGRAM: ChannelPlatform = "telegram"


class WorkspaceChannelRoleValues:
    """``workspace_channels.role`` constants.

    * ``owned`` — our bot is admin in this channel; we can post.
    * ``competitor`` — a public channel we watch via user-bot (D40
      in ``docs/05 §5.2``); read-only. Added in PR #18.
    """

    OWNED: WorkspaceChannelRole = "owned"
    COMPETITOR: WorkspaceChannelRole = "competitor"


_CHANNEL_PLATFORMS: tuple[ChannelPlatform, ...] = (ChannelPlatformValues.TELEGRAM,)
_WORKSPACE_CHANNEL_ROLES: tuple[WorkspaceChannelRole, ...] = (
    WorkspaceChannelRoleValues.OWNED,
    WorkspaceChannelRoleValues.COMPETITOR,
)


class Channel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Global Channel Registry row.

    NOT tenant-scoped — the same channel can be connected from
    multiple workspaces (post-MVP agency mode) and we want a single
    ingest pipeline, not N. Tenant isolation is enforced one layer
    up, on :class:`WorkspaceChannel`.
    """

    __tablename__ = "channels"
    __table_args__ = (
        CheckConstraint(
            "platform IN ('telegram')",
            name="ck_channels_platform",
        ),
        UniqueConstraint(
            "platform",
            "external_id",
            name="uq_channels_platform_external",
        ),
        Index("ix_channels_platform_username", "platform", "username"),
    )

    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    external_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text(), nullable=True)
    subscribers_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_public: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        default=False,
        server_default="false",
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        return f"<Channel {self.platform}:{self.external_id} @{self.username}>"


class WorkspaceChannel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Connection between a brand and a channel inside a workspace.

    RLS-isolated by ``workspace_id`` (policy installed in migration
    ``0010_channels_registry``). Soft-detach via
    ``disconnected_at`` so the audit trail and post history stay
    intact when the user removes a channel — Sprint 8 retention
    finally drops the rows together with their post history.
    """

    __tablename__ = "workspace_channels"
    __table_args__ = (
        CheckConstraint(
            "role IN ('owned', 'competitor')",
            name="ck_workspace_channels_role",
        ),
        UniqueConstraint(
            "workspace_id",
            "brand_id",
            "channel_id",
            name="uq_workspace_channels_brand_channel",
        ),
        Index("ix_workspace_channels_brand", "brand_id"),
        Index("ix_workspace_channels_channel", "channel_id"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    brand_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("brands.id", ondelete="CASCADE"),
        nullable=False,
    )
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channels.id", ondelete="RESTRICT"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=WorkspaceChannelRoleValues.OWNED,
        server_default=WorkspaceChannelRoleValues.OWNED,
    )
    # Snapshot of the bot's admin rights at connect / re-verify
    # time. JSON shape mirrors ``aiogram`` ``ChatMemberAdministrator``:
    # ``{"can_post_messages": true, "can_edit_messages": true,
    # "can_delete_messages": true, "captured_at": "..."}``.
    bot_admin_rights: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
        default=dict,
        server_default="{}",
    )
    connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    disconnected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    def __repr__(self) -> str:
        return (
            f"<WorkspaceChannel ws={self.workspace_id} "
            f"brand={self.brand_id} channel={self.channel_id} role={self.role}>"
        )


class ChannelPost(Base):
    """One observed post on a channel.

    The Postgres table is monthly-partitioned by ``posted_at`` via
    pg_partman (see migration ``0010_channels_registry``). The
    composite primary key on Postgres is ``(id, posted_at)`` — the
    ORM treats ``id`` as the mapper identity since lookups in
    application code are always by ``id`` or by
    ``(channel_id, tg_message_id)``.
    """

    __tablename__ = "channel_posts"
    __table_args__ = (
        # The Postgres-side composite unique constraint includes
        # ``posted_at`` (partition key); the SQLite test schema
        # uses the same logical uniqueness on ``(channel_id,
        # tg_message_id)``. We declare the latter here so the
        # ORM-managed model can run hermetic tests; on Postgres
        # the matching constraint is installed by the migration
        # at the partition level.
        UniqueConstraint(
            "channel_id",
            "tg_message_id",
            name="uq_channel_posts_channel_tg_message",
        ),
        Index(
            "ix_channel_posts_channel_posted",
            "channel_id",
            "posted_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channels.id", ondelete="CASCADE"),
        nullable=False,
    )
    tg_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    text: Mapped[str | None] = mapped_column(Text(), nullable=True)
    entities: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=True,
    )
    has_media: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        default=False,
        server_default="false",
    )
    media_summary: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=True,
    )
    views_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reactions_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    forwards_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    posted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        return f"<ChannelPost channel={self.channel_id} tg_msg={self.tg_message_id}>"


__all__ = [
    "Channel",
    "ChannelPlatform",
    "ChannelPlatformValues",
    "ChannelPost",
    "WorkspaceChannel",
    "WorkspaceChannelRole",
    "WorkspaceChannelRoleValues",
]
