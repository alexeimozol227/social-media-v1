"""FX rate snapshot model (PR #20).

docs/plans/phase1-sprint3-plan.md §2.1.3 + docs/04-architecture.md
§22.2: every ``llm_calls`` / ``agent_runs`` row carries both
``cost_usd`` (provider-native) and ``cost_rub`` (workspace-default
currency snapshot). ``cost_rub`` is computed at write time using
the most recent ``fx_rates`` row for ``(base='USD', quote='RUB')``;
if the table is empty (fresh dev DB) the writer falls back to
``Settings.usd_to_rub_fallback`` and logs a warning so on-call
sees the gap.

The table is intentionally append-only: snapshots are inserted on
a schedule (cbr.ru daily fetch — wired in Sprint 8) and the
service reads the latest by ``observed_at DESC``. Updating the
"current" rate is a fresh INSERT, never an UPDATE — historical
rows give the admin dashboard an audit trail of how the rate
moved over time.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class FxRate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One observed FX rate snapshot.

    The natural key is ``(base_currency, quote_currency, observed_at)``;
    a UNIQUE constraint on those three columns blocks duplicate
    snapshots from the same source on the same minute.
    """

    __tablename__ = "fx_rates"

    base_currency: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        doc="ISO-4217 base currency (e.g. ``USD``).",
    )
    quote_currency: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        doc="ISO-4217 quote currency (e.g. ``RUB``).",
    )
    rate: Mapped[Decimal] = mapped_column(
        Numeric(18, 8),
        nullable=False,
        doc=(
            "Conversion rate: 1 unit of ``base_currency`` equals ``rate`` "
            "units of ``quote_currency``."
        ),
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
        doc="When the upstream feed reported this rate.",
    )
    source: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="manual",
        server_default="manual",
        doc=(
            "Origin of the snapshot — ``manual`` for ops-set rows, "
            "``cbr.ru`` for the daily fetch wired in Sprint 8."
        ),
    )


__all__ = ["FxRate"]
