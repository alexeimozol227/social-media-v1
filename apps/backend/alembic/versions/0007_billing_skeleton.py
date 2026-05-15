"""create plans, plan_prices, invoices tables

Revision ID: 0007_billing_skeleton
Revises: 0006_idempotency_keys
Create Date: 2026-05-15

PR #10 / phase0-phase1-sprint1-plan.md — Multi-currency billing
skeleton (docs/04 §10.6, docs/07).

Three tables:
  * ``plans`` — pricing tier catalog (solo / pro / network).
  * ``plan_prices`` — per-currency, per-period prices with
    effective-date versioning.
  * ``invoices`` — one row per charge event, keyed by workspace.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "0007_billing_skeleton"
down_revision: str = "0006_idempotency_keys"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    # ---- plans ----
    op.create_table(
        "plans",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(32), nullable=False, unique=True),
        sa.Column("tier", sa.String(32), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("max_brands", sa.Integer, nullable=False, server_default="1"),
        sa.Column("max_posts_per_month", sa.Integer, nullable=False, server_default="30"),
        sa.Column("max_ai_text_per_month", sa.Integer, nullable=False, server_default="100"),
        sa.Column("max_ai_media_per_month", sa.Integer, nullable=False, server_default="30"),
        sa.Column("max_channels_per_brand", sa.Integer, nullable=False, server_default="1"),
        sa.Column("max_competitors", sa.Integer, nullable=False, server_default="5"),
        sa.Column("features", sa.dialects.postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("enabled_agents", sa.dialects.postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_plans_code", "plans", ["code"], unique=True)

    # ---- plan_prices ----
    op.create_table(
        "plan_prices",
        sa.Column("plan_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("plans.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("currency", sa.String(3), primary_key=True),
        sa.Column("period", sa.String(10), primary_key=True),
        sa.Column("effective_from", sa.DateTime(timezone=True), server_default=sa.func.now(), primary_key=True),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
    )
    op.create_index(
        "ix_plan_prices_lookup",
        "plan_prices",
        ["plan_id", "currency", "period"],
    )

    # ---- invoices ----
    op.create_table(
        "invoices",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plan_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("plans.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("reference_amount_usd", sa.Numeric(10, 2), nullable=True),
        sa.Column("exchange_rate", sa.Numeric(10, 6), nullable=True),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_invoices_workspace_status", "invoices", ["workspace_id", "status"])
    op.create_index("ix_invoices_workspace_period", "invoices", ["workspace_id", "period_start"])


def downgrade() -> None:
    op.drop_table("invoices")
    op.drop_table("plan_prices")
    op.drop_table("plans")
