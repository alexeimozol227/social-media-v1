"""Plan catalog ORM model (docs/04 §10.6, docs/07).

One row per pricing tier (``solo``, ``pro``, ``network``).  Limits and
feature flags live in ``features`` (JSONB) so bumping a cap is a
single-row UPDATE — no code-deploy or migration required.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Boolean, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Plan(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One row per pricing tier."""

    __tablename__ = "plans"

    code: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        unique=True,
        index=True,
    )
    tier: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    max_brands: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    max_posts_per_month: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    max_ai_text_per_month: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    max_ai_media_per_month: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    max_channels_per_brand: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    max_competitors: Mapped[int] = mapped_column(Integer, nullable=False, default=5)

    features: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
        default=dict,
    )
    enabled_agents: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
        default=dict,
    )

    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
