"""Billing Pydantic schemas (docs/04 §10.6, docs/07)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

# ---- Plan ----


class PlanRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    tier: str
    name: str
    description: str | None = None
    max_brands: int
    max_posts_per_month: int
    max_ai_text_per_month: int
    max_ai_media_per_month: int
    max_channels_per_brand: int
    max_competitors: int
    features: dict[str, object]
    enabled_agents: dict[str, object]
    active: bool
    sort_order: int


# ---- PlanPrice ----


class PlanPriceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    plan_id: uuid.UUID
    currency: str
    period: str
    amount: Decimal
    effective_from: datetime
    effective_to: datetime | None = None


class PlanWithPrices(PlanRead):
    prices: list[PlanPriceRead] = Field(default_factory=list)


# ---- Invoice ----


class InvoiceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    plan_id: uuid.UUID
    amount: Decimal
    currency: str
    reference_amount_usd: Decimal | None = None
    exchange_rate: Decimal | None = None
    period_start: datetime
    period_end: datetime
    status: str
    created_at: datetime
