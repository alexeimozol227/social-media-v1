"""seed the plan catalog (solo / pro / network) + multi-currency prices

Revision ID: 0013_seed_plan_catalog
Revises: 0012_telegram_userbot_sessions
Create Date: 2026-05-16

Materialises the ``plans`` + ``plan_prices`` rows defined in
:mod:`app.services.billing.seed` so that
:func:`app.services.billing.plans.get_active_plan_for_workspace`
resolves the ``solo`` fallback on a fresh DB without any paid
invoices (docs/07 §2.1, docs/04 §10.6).

The schema migration that creates these tables landed in
``0007_billing_skeleton`` but the data side was missing, which left
every brand-quota / plan-lookup endpoint raising
``PlanNotConfiguredError: Plan catalog not seeded`` on a fresh
``alembic upgrade head``. The seed module is the single source of
truth, so this migration imports the constants directly rather than
duplicating the table.

Idempotent: each insert is guarded by a ``SELECT`` on the natural
key (``plans.code`` and ``plan_prices.(plan_id, currency, period)``
with ``effective_to IS NULL``) so re-running ``alembic upgrade
head`` on an already-seeded DB is a no-op. Manual operator edits
(``UPDATE plans SET max_brands = ...``) are also preserved on
re-up.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.services.billing.seed import PLAN_SEED, PRICE_SEED

# revision identifiers, used by Alembic.
revision: str = "0013_seed_plan_catalog"
down_revision: str | None = "0012_telegram_userbot_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _plans_table() -> sa.Table:
    """Minimal ``plans`` table reflection for INSERT/SELECT.

    Declared inline so the migration stays decoupled from the ORM —
    a future model rename / column drop won't retroactively break a
    historical migration.
    """

    return sa.table(
        "plans",
        sa.column("id", sa.dialects.postgresql.UUID(as_uuid=True)),
        sa.column("code", sa.String),
        sa.column("tier", sa.String),
        sa.column("name", sa.String),
        sa.column("description", sa.Text),
        sa.column("max_brands", sa.Integer),
        sa.column("max_posts_per_month", sa.Integer),
        sa.column("max_ai_text_per_month", sa.Integer),
        sa.column("max_ai_media_per_month", sa.Integer),
        sa.column("max_channels_per_brand", sa.Integer),
        sa.column("max_competitors", sa.Integer),
        sa.column("features", sa.dialects.postgresql.JSONB),
        sa.column("enabled_agents", sa.dialects.postgresql.JSONB),
        sa.column("active", sa.Boolean),
        sa.column("sort_order", sa.Integer),
    )


def _plan_prices_table() -> sa.Table:
    return sa.table(
        "plan_prices",
        sa.column("plan_id", sa.dialects.postgresql.UUID(as_uuid=True)),
        sa.column("currency", sa.String),
        sa.column("period", sa.String),
        sa.column("amount", sa.Numeric(10, 2)),
    )


def upgrade() -> None:
    bind = op.get_bind()
    plans = _plans_table()
    prices = _plan_prices_table()

    for spec in PLAN_SEED:
        code = spec["code"]
        existing_id = bind.execute(
            sa.select(plans.c.id).where(plans.c.code == code),
        ).scalar_one_or_none()
        if existing_id is None:
            plan_id = uuid.uuid4()
            bind.execute(
                plans.insert().values(
                    id=plan_id,
                    code=code,
                    tier=spec["tier"],
                    name=spec["name"],
                    description=spec.get("description"),
                    max_brands=spec["max_brands"],
                    max_posts_per_month=spec["max_posts_per_month"],
                    max_ai_text_per_month=spec["max_ai_text_per_month"],
                    max_ai_media_per_month=spec["max_ai_media_per_month"],
                    max_channels_per_brand=spec["max_channels_per_brand"],
                    max_competitors=spec["max_competitors"],
                    features=spec["features"],
                    enabled_agents=spec["enabled_agents"],
                    active=True,
                    sort_order=spec["sort_order"],
                ),
            )
        else:
            plan_id = existing_id

        for currency, period, amount in PRICE_SEED.get(code, []):
            already_priced = bind.execute(
                sa.select(prices.c.plan_id).where(
                    prices.c.plan_id == plan_id,
                    prices.c.currency == currency,
                    prices.c.period == period,
                ),
            ).first()
            if already_priced is not None:
                continue
            bind.execute(
                prices.insert().values(
                    plan_id=plan_id,
                    currency=currency,
                    period=period,
                    amount=amount,
                ),
            )


def downgrade() -> None:
    # Only delete rows whose ``code`` matches the seed. Operators may
    # have inserted custom tiers (e.g. ``enterprise``) on top — those
    # stay.
    bind = op.get_bind()
    plans = _plans_table()
    prices = _plan_prices_table()
    codes = [spec["code"] for spec in PLAN_SEED]

    plan_ids = [
        row[0]
        for row in bind.execute(
            sa.select(plans.c.id).where(plans.c.code.in_(codes)),
        ).all()
    ]
    if plan_ids:
        bind.execute(prices.delete().where(prices.c.plan_id.in_(plan_ids)))
        bind.execute(plans.delete().where(plans.c.id.in_(plan_ids)))
