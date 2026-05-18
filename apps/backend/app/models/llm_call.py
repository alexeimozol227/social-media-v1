"""``llm_calls`` ORM model (PR #20).

docs/04-architecture.md §10.4 + docs/plans/phase1-sprint3-plan.md
§2.1.2: one row per provider HTTP round-trip. An agent run
typically fires N llm-calls (tool-calling loop), so the parent
:class:`~app.models.agent_run.AgentRun` carries denormalised
totals while this table holds the per-call detail.

``prompt_full`` and ``raw_output`` are only persisted when the
workspace owner opted in to training-data use; otherwise the
columns are NULL from the very first write so a later policy
flip doesn't leak retention-zeroed history.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
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


class LLMCallType:
    """Constants for ``llm_calls.call_type``."""

    CHAT = "chat"
    EMBED = "embed"
    IMAGE = "image"


_CALL_TYPE_VALUES = (LLMCallType.CHAT, LLMCallType.EMBED, LLMCallType.IMAGE)


class CircuitBreakerState:
    """Constants for ``llm_calls.circuit_breaker_state``."""

    CLOSED = "closed"
    HALF_OPEN = "half_open"
    OPEN = "open"


_CB_VALUES = (
    CircuitBreakerState.CLOSED,
    CircuitBreakerState.HALF_OPEN,
    CircuitBreakerState.OPEN,
)


class LLMCall(Base):
    """One provider HTTP round-trip."""

    __tablename__ = "llm_calls"
    __table_args__ = (
        CheckConstraint(
            "call_type IN ('chat', 'embed', 'image')",
            name="ck_llm_calls_call_type",
        ),
        CheckConstraint(
            "circuit_breaker_state IN ('closed', 'half_open', 'open')",
            name="ck_llm_calls_breaker_state",
        ),
        Index(
            "ix_llm_calls_agent_run",
            "agent_run_id",
            "created_at",
        ),
        Index(
            "ix_llm_calls_workspace_created",
            "workspace_id",
            "created_at",
        ),
        Index(
            "ix_llm_calls_provider_model_created",
            "provider",
            "model",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    agent_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    brand_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(96), nullable=False)
    call_type: Mapped[str] = mapped_column(String(16), nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_full: Mapped[str | None] = mapped_column(Text(), nullable=True)
    raw_output: Mapped[str | None] = mapped_column(Text(), nullable=True)
    tools_called: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
        default=list,
        server_default="[]",
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
    input_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(14, 6),
        nullable=False,
        default=Decimal("0"),
        server_default="0",
    )
    output_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(14, 6),
        nullable=False,
        default=Decimal("0"),
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
    latency_ms: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
        default=0,
        server_default="0",
    )
    circuit_breaker_state: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=CircuitBreakerState.CLOSED,
        server_default=CircuitBreakerState.CLOSED,
    )
    retries: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
        default=0,
        server_default="0",
    )
    success: Mapped[bool] = mapped_column(Boolean(), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    response_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
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


__all__ = ["CircuitBreakerState", "LLMCall", "LLMCallType"]
