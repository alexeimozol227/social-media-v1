"""Invoice ORM model (docs/04 §10.6, docs/07 §6).

Every charge cycle (monthly or annual) produces one invoice row.
Prorated upgrade charges also create an invoice.  The ``status``
transitions are:

    draft → open → paid | void | failed

``reference_amount_usd`` + ``exchange_rate`` are frozen at charge time
so that revenue dashboards can always aggregate in a single currency.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Invoice(Base):
    """One row per charge event."""

    __tablename__ = "invoices"
    __table_args__ = (
        Index("ix_invoices_workspace_status", "workspace_id", "status"),
        Index("ix_invoices_workspace_period", "workspace_id", "period_start"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("plans.id", ondelete="RESTRICT"),
        nullable=False,
    )

    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    reference_amount_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 2), nullable=True,
    )
    exchange_rate: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 6), nullable=True,
    )

    period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    period_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="draft",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
