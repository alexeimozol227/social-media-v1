"""ChannelPostEmbedding ORM model (PR #17).

docs/04-architecture.md §19.6 + docs/plans/phase1-sprint2-plan.md
PR #17: one row per ``(channel_post_id, model)`` carrying the
pgvector embedding of the post's text (+ caption). Sprint 3 wires
the Brand Memory + Analyst agents on top of this table; PR #17
only puts the schema, the persistence path, and the embedding
provider scaffolding in place.

Dialect handling
----------------
* On **Postgres** the ``embedding`` column is ``vector(EMBEDDING_DIM)``
  via :class:`pgvector.sqlalchemy.Vector` (the wheel is part of the
  Postgres-side image; the import is guarded so SQLite test runs
  don't require it).
* On **SQLite** (the test DB) the column is ``JSON`` — a
  :class:`~sqlalchemy.types.TypeDecorator` serialises the list of
  floats on write and parses it back on read so the ORM API is
  identical across dialects.

The migration ``0011_channel_post_embeddings`` partitions the
table by ``created_at`` on Postgres; the ORM treats ``id`` as the
mapper identity since lookups in application code go through
``(channel_post_id, model)``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from app.db.base import Base

# Embedding dimensionality — mirrors ``settings.embedding_dim`` and
# the migration's hardcoded value. Centralised here so the ORM /
# tests / mock provider stay in sync.
EMBEDDING_DIM = 1536


class Vector(TypeDecorator[list[float]]):
    """Cross-dialect column type for pgvector embeddings.

    * On Postgres, hands the column off to
      :class:`pgvector.sqlalchemy.Vector` so SQL generation emits
      ``vector(DIM)`` and the result rows materialise as
      ``numpy.ndarray`` / ``list[float]`` directly (no manual JSON
      parse).
    * On SQLite (and any other dialect we don't recognise), falls
      back to JSON: the embedding round-trips through the column as
      a ``list[float]`` and the application code never sees a
      ``str``.

    Why not require pgvector in the ORM import? The unit-test suite
    runs on aiosqlite, where the pgvector wheel isn't even on
    ``sys.path``. Guarding the import keeps the test surface light;
    when production runs against Postgres, ``load_dialect_impl`` is
    the only place the wheel is touched.
    """

    # ``cache_ok = True`` opts into SQLAlchemy's per-statement compiled
    # cache. The TypeDecorator is immutable (``dim`` is the only
    # parameter and it's per-instance), so caching by class+dim is
    # safe and lets the executemany() path stay zero-allocation.
    cache_ok = True
    impl = JSON

    def __init__(self, dim: int = EMBEDDING_DIM, *args: Any, **kwargs: Any) -> None:
        self.dim = dim
        super().__init__(*args, **kwargs)

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name == "postgresql":
            # Lazy import — keeps the wheel out of the SQLite test
            # path (see the class docstring).
            try:
                from pgvector.sqlalchemy import Vector as PgVector  # type: ignore[import-untyped]
            except ImportError:  # pragma: no cover - production has pgvector
                # Defensive: if pgvector isn't installed in a Postgres
                # environment we surface a clear ImportError at column
                # bind time instead of failing with an opaque
                # "unknown type" deep inside SQLAlchemy.
                raise ImportError(
                    "pgvector is required to use Vector columns on Postgres; "
                    "add 'pgvector' to the backend dependencies."
                ) from None
            return dialect.type_descriptor(PgVector(self.dim))
        # SQLite / other dialects: round-trip via JSON.
        return dialect.type_descriptor(JSON())

    def process_bind_param(
        self,
        value: list[float] | None,
        dialect: Dialect,
    ) -> Any:
        if value is None:
            return None
        # On Postgres pgvector accepts list[float] directly and
        # validates dim — let it do its own validation. On SQLite we
        # store the raw list as JSON; the dim guard lives in the
        # service layer (``EmbeddingsService`` raises a typed
        # ``LLMProviderError`` on dim mismatch).
        return list(value)

    def process_result_value(
        self,
        value: Any,
        dialect: Dialect,
    ) -> list[float] | None:
        if value is None:
            return None
        # pgvector returns a ``numpy.ndarray`` on read; coerce to
        # ``list[float]`` so callers don't need numpy installed.
        if isinstance(value, list):
            return [float(x) for x in value]
        # Best-effort: anything else (e.g. numpy array) is iterable
        # and yields floats.
        return [float(x) for x in value]


class ChannelPostEmbedding(Base):
    """One embedding row per ``(channel_post_id, model)`` pair.

    Persistence path:

    1. The Celery task :func:`app.workers.tasks.embed_channel_post`
       fetches the post text from ``channel_posts``.
    2. :class:`app.services.embeddings.EmbeddingsService` resolves
       the binding's ``workspace_id`` via ``workspace_channels``,
       asks the configured :class:`~app.adapters.llm.LLMProvider`
       for a vector, and upserts here.
    3. Re-running the task is idempotent: the unique constraint on
       ``(channel_post_id, model, created_at)`` is the conflict
       target. The service treats a hit as "row exists, update the
       embedding in place" rather than letting the IntegrityError
       propagate.

    No SQLAlchemy ``relationship()`` to :class:`ChannelPost` because
    on Postgres the parent table is partitioned and SQLAlchemy joins
    across partitioned tables get noisy — application code resolves
    the join explicitly in the service layer.
    """

    __tablename__ = "channel_post_embeddings"
    __table_args__ = (
        UniqueConstraint(
            "channel_post_id",
            "model",
            name="uq_channel_post_embeddings_post_model",
        ),
        Index(
            "ix_channel_post_embeddings_ws_channel",
            "workspace_id",
            "channel_id",
        ),
        Index(
            "ix_channel_post_embeddings_post",
            "channel_post_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    channel_post_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channels.id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        return (
            f"<ChannelPostEmbedding post={self.channel_post_id} "
            f"model={self.model} dim={len(self.embedding) if self.embedding else 0}>"
        )


__all__ = [
    "EMBEDDING_DIM",
    "ChannelPostEmbedding",
    "Vector",
]
