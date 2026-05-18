"""Brand Memory ORM models (PR #21).

docs/04-architecture.md §4 + docs/plans/phase1-sprint3-plan.md
PR #21 — two-layer Brand Memory (П11 single source of truth):

* :class:`BrandMemoryCore` — one row per brand. ``payload`` is the
  canonical JSONB blob (tone-of-voice, audience, taboos, etc.).
* :class:`BrandMemoryOverlay` — per-channel override applied on top
  of the core; keyed on ``(brand_id, workspace_channel_id)``.
* :class:`BrandMemoryExample` — vector-indexed brand-post snippets
  used by ``BrandMemoryService.search_examples``. Mirrors
  :class:`app.models.channel_post_embedding.ChannelPostEmbedding` —
  pgvector on Postgres, JSON-list fallback on SQLite.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
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
from sqlalchemy.types import DateTime

from app.db.base import Base
from app.models.channel_post_embedding import EMBEDDING_DIM, Vector


class BrandMemoryCore(Base):
    """Brand-wide tone / audience / taboos payload (one row per brand).

    Optimistic-concurrency control via ``version``: every PATCH that
    actually mutates the payload bumps the counter so a stale SPA
    can detect the conflict client-side. ``updated_by_user_id`` and
    ``updated_by_agent`` attribute the last change to the responsible
    actor (manual UI edit vs. OnboardingAgent extraction in PR #22).
    """

    __tablename__ = "brand_memory_core"
    __table_args__ = (
        UniqueConstraint("brand_id", name="uq_brand_memory_core_brand"),
        Index("ix_brand_memory_core_workspace", "workspace_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
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
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
        default=dict,
        server_default="{}",
    )
    version: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
        default=1,
        server_default="1",
    )
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_by_agent: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return f"<BrandMemoryCore brand={self.brand_id} v={self.version}>"


class BrandMemoryOverlay(Base):
    """Per-channel override applied on top of :class:`BrandMemoryCore`.

    Sparse: a missing row means "no overlay → fall back to core". The
    service layer merges ``core.payload`` and ``overlay.payload``
    shallowly (overlay keys win) before handing the result to the
    agent. Same versioning / attribution surface as
    :class:`BrandMemoryCore`.
    """

    __tablename__ = "brand_memory_overlays"
    __table_args__ = (
        UniqueConstraint(
            "brand_id",
            "workspace_channel_id",
            name="uq_brand_memory_overlays_brand_channel",
        ),
        Index("ix_brand_memory_overlays_workspace", "workspace_id"),
        Index("ix_brand_memory_overlays_brand", "brand_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
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
    workspace_channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspace_channels.id", ondelete="CASCADE"),
        nullable=False,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
        default=dict,
        server_default="{}",
    )
    version: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
        default=1,
        server_default="1",
    )
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_by_agent: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return (
            f"<BrandMemoryOverlay brand={self.brand_id} "
            f"channel={self.workspace_channel_id} v={self.version}>"
        )


class BrandMemoryExample(Base):
    """Vector-indexed brand-post snippet for similarity search (PR #21).

    Populated by the OnboardingAgent and the embedding pipeline; read
    by :class:`~app.services.brand_memory.BrandMemoryService.search_examples`
    to anchor the Content Agent draft against representative posts.
    Mirrors :class:`app.models.channel_post_embedding.ChannelPostEmbedding`:
    pgvector on Postgres (HNSW index lives on the partition template
    installed in migration 0015), JSON-list fallback on SQLite via the
    :class:`Vector` TypeDecorator.

    The Postgres-side primary key includes the partition column
    (``created_at``) per pg_partman rules; the ORM only ever looks up
    by ``id`` or by ``(brand_id, …)`` so the composite PK is invisible
    to application code.
    """

    __tablename__ = "brand_memory_examples"
    __table_args__ = (
        Index(
            "ix_brand_memory_examples_ws_brand",
            "workspace_id",
            "brand_id",
        ),
        Index(
            "ix_brand_memory_examples_brand_created",
            "brand_id",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
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
    # ``source_channel_post_id`` is intentionally not declared with a
    # foreign key: ``channel_posts`` is partitioned (PR #11) and Postgres
    # doesn't support FKs into a partitioned table without the partition
    # key in the constraint. The application enforces referential integrity.
    source_channel_post_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    text_snippet: Mapped[str] = mapped_column(Text(), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(
        Vector(dim=EMBEDDING_DIM),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        return (
            f"<BrandMemoryExample brand={self.brand_id} "
            f"model={self.model} post={self.source_channel_post_id}>"
        )


__all__ = [
    "BrandMemoryCore",
    "BrandMemoryExample",
    "BrandMemoryOverlay",
]
