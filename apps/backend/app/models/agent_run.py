"""``agent_runs`` ORM model (PR #20).

docs/04-architecture.md §10.4 + docs/plans/phase1-sprint3-plan.md
§2.1.1: one row per agent invocation, carrying denormalised cost /
token totals + chain-of-thought (when the user opted in to
training data) + linked parent run for orchestrated multi-agent
flows.

Status axis (``ck_agent_runs_status``):
* ``started``   — :class:`AgentRunWriter.start_run` inserted the row.
* ``succeeded`` — :class:`AgentRunWriter.finish_run` marked OK.
* ``failed``    — :class:`AgentRunWriter.finish_run` marked failure.
* ``cancelled`` — operator cancelled (Sprint 8 admin tooling).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AgentRunStatus:
    """Constants for ``agent_runs.status``.

    Plain ``String(16)`` + CHECK constraint so adding a value is a
    CHECK swap, not an enum-type ALTER.
    """

    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


_STATUS_VALUES = (
    AgentRunStatus.STARTED,
    AgentRunStatus.SUCCEEDED,
    AgentRunStatus.FAILED,
    AgentRunStatus.CANCELLED,
)


class AgentRun(Base):
    """One agent invocation, end-to-end."""

    __tablename__ = "agent_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('started', 'succeeded', 'failed', 'cancelled')",
            name="ck_agent_runs_status",
        ),
        Index(
            "ix_agent_runs_workspace_started",
            "workspace_id",
            "started_at",
        ),
        Index(
            "ix_agent_runs_brand_started",
            "brand_id",
            "started_at",
        ),
        Index(
            "ix_agent_runs_agent",
            "agent",
            "started_at",
        ),
        Index(
            "ix_agent_runs_parent",
            "parent_run_id",
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
    brand_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("brands.id", ondelete="SET NULL"),
        nullable=True,
    )
    agent: Mapped[str] = mapped_column(String(64), nullable=False)
    agent_version: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="v0",
        server_default="v0",
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=AgentRunStatus.STARTED,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    latency_ms: Mapped[int | None] = mapped_column(
        BigInteger(),
        nullable=True,
    )
    prompt_tokens: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
        default=0,
        server_default="0",
    )
    completion_tokens: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
        default=0,
        server_default="0",
    )
    cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(14, 6),
        nullable=False,
        default=Decimal("0"),
        server_default="0",
    )
    cost_rub: Mapped[Decimal] = mapped_column(
        Numeric(14, 4),
        nullable=False,
        default=Decimal("0"),
        server_default="0",
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text(), nullable=True)
    chain_of_thought: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=True,
    )
    retrieved_context: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=True,
    )
    skills_used: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
        default=list,
        server_default="[]",
    )
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    idempotency_key: Mapped[str | None] = mapped_column(Text(), nullable=True)
    originator_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    opt_in_training: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        default=False,
        server_default="false",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


__all__ = ["AgentRun", "AgentRunStatus"]
