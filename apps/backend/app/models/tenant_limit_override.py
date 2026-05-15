"""TenantLimitOverride ORM model (docs/04 §10.6, §16.4 + docs/06 §5 Спринт 1).

Per-workspace VIP / promo / pilot overrides for the plan limits in
:class:`app.models.plan.Plan`. Resolution semantics (D-level
quotas, see :mod:`app.services.billing.quotas`):

* NULL in any ``max_*`` column → the workspace inherits the plan's
  baseline.
* Non-NULL value → that column overrides the plan baseline until
  ``valid_until`` passes (NULL = indefinite).

One row per workspace today; the schema allows multiple rows in
the future (different effective windows / stacked promos) without
a migration — the resolver picks the most recently created row
whose ``valid_until`` is unset or in the future.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class TenantLimitOverride(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """VIP / promo / pilot override row.

    Columns are 1:1 with the plan ceilings the override is allowed
    to bump (``max_brands``, ``max_posts_per_month``,
    ``max_tokens_per_month``, ``max_usd_per_month``). Anything not
    represented here falls through to the plan baseline.

    ``reason`` is free-form audit context (the admin UI will require
    it on every write so the row is self-explanatory in a future
    investigation).
    """

    __tablename__ = "tenant_limit_overrides"
    __table_args__ = (
        Index(
            "ix_tenant_limit_overrides_workspace_valid_until",
            "workspace_id",
            "valid_until",
        ),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Each ``max_*`` is nullable so an override can bump a single
    # dimension without having to restate the entire ceiling vector.
    max_brands: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_posts_per_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_tokens_per_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_usd_per_month: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 2),
        nullable=True,
    )

    # ``NULL`` = override never expires. The resolver treats both
    # ``NULL`` and a future timestamp as "active".
    valid_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Audit context for the override (free-form). Required at the
    # API layer; nullable in the model so a hand-inserted row from
    # a migration / seed doesn't fail.
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


__all__ = ["TenantLimitOverride"]
