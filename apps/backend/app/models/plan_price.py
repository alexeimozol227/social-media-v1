"""Multi-currency plan pricing (docs/04 §10.6, docs/07 §2.1).

Each plan has one row per (currency, period) pair. ``effective_from``
/ ``effective_to`` allow price changes without losing historical data.
The composite PK ``(plan_id, currency, period, effective_from)``
guarantees that exactly one price is active for each combination at
any given time.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PlanPrice(Base):
    """Price for a given plan in a specific currency + billing period."""

    __tablename__ = "plan_prices"
    __table_args__ = (
        Index(
            "ix_plan_prices_lookup",
            "plan_id", "currency", "period",
        ),
    )

    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("plans.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    currency: Mapped[str] = mapped_column(
        String(3), primary_key=True, nullable=False,
    )
    period: Mapped[str] = mapped_column(
        String(10), primary_key=True, nullable=False,
    )
    effective_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        primary_key=True,
        server_default=func.now(),
        nullable=False,
    )
    effective_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    amount: Mapped[int] = mapped_column(
        Numeric(10, 2), nullable=False,
    )
