"""``llm_calls_daily`` ORM model (PR #20).

docs/plans/phase1-sprint3-plan.md §2.1.2 + docs/04-architecture.md
§18.5: monthly-partitioned aggregate of
:class:`~app.models.llm_call.LLMCall` rows. Sprint 8's retention
job rolls the raw rows into this table at 90 days and deletes
them; until then the table just lives empty.

The natural key is
``(workspace_id, brand_id, date, provider, model)``. ``brand_id``
is nullable so workspace-scoped runs (e.g. healthcheck) aggregate
into a single bucket per ``(workspace, date, provider, model)``.
"""

from __future__ import annotations

import uuid
from datetime import date as date_
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Index,
    Numeric,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LLMCallDaily(Base):
    """One aggregated row per ``(workspace, brand, date, provider, model)``."""

    __tablename__ = "llm_calls_daily"
    __table_args__ = (
        PrimaryKeyConstraint(
            "workspace_id",
            "brand_id",
            "date",
            "provider",
            "model",
            "id",
            name="pk_llm_calls_daily",
        ),
        UniqueConstraint(
            "workspace_id",
            "brand_id",
            "date",
            "provider",
            "model",
            name="uq_llm_calls_daily_natural",
        ),
        Index(
            "ix_llm_calls_daily_ws_date",
            "workspace_id",
            "date",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        default=uuid.uuid4,
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
    date: Mapped[date_] = mapped_column(Date(), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(96), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(
        BigInteger(),
        nullable=False,
        default=0,
        server_default="0",
    )
    completion_tokens: Mapped[int] = mapped_column(
        BigInteger(),
        nullable=False,
        default=0,
        server_default="0",
    )
    cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(16, 6),
        nullable=False,
        default=Decimal("0"),
        server_default="0",
    )
    cost_rub: Mapped[Decimal] = mapped_column(
        Numeric(16, 4),
        nullable=False,
        default=Decimal("0"),
        server_default="0",
    )
    call_count: Mapped[int] = mapped_column(
        BigInteger(),
        nullable=False,
        default=0,
        server_default="0",
    )
    errors_count: Mapped[int] = mapped_column(
        BigInteger(),
        nullable=False,
        default=0,
        server_default="0",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


__all__ = ["LLMCallDaily"]
