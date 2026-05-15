"""create tenant_limit_overrides table

Revision ID: 0009_tenant_limit_overrides
Revises: 0008_rls_policies
Create Date: 2026-05-15

PR #12 — docs/04-architecture.md §10.6 + §16.4 + docs/06-roadmap.md
§5 Сприннт 1 ("`tenant_limit_overrides` — VIP / promo / pilot
переопределения").

VIP / promo / pilot overrides for the per-workspace plan limits
in :class:`app.models.plan.Plan`. NULL in a ``max_*`` column = inherit
the plan baseline; non-NULL = override until ``valid_until`` passes.

The companion read-through resolver lives in
:mod:`app.services.billing.quotas`. The admin Settings UI for
mutating this table is reserved for a future PR — for the moment
operators set rows by hand via psql.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0009_tenant_limit_overrides"
down_revision: str = "0008_rls_policies"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.create_table(
        "tenant_limit_overrides",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "workspace_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("max_brands", sa.Integer, nullable=True),
        sa.Column("max_posts_per_month", sa.Integer, nullable=True),
        sa.Column("max_tokens_per_month", sa.Integer, nullable=True),
        sa.Column("max_usd_per_month", sa.Numeric(10, 2), nullable=True),
        sa.Column(
            "valid_until",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column(
            "created_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_tenant_limit_overrides_workspace_valid_until",
        "tenant_limit_overrides",
        ["workspace_id", "valid_until"],
    )

    # Postgres-only: enable RLS so admins editing overrides through the
    # API can't accidentally cross tenants. The resolver bypasses RLS
    # by running as ``app.platform_role = 'admin'``; the production
    # write path goes through the admin module which already sets that
    # GUC at request time.
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute(
        "ALTER TABLE tenant_limit_overrides ENABLE ROW LEVEL SECURITY;",
    )
    op.execute(
        "ALTER TABLE tenant_limit_overrides FORCE ROW LEVEL SECURITY;",
    )
    op.execute(
        """
        CREATE POLICY tenant_limit_overrides_isolation
            ON tenant_limit_overrides
            USING (
                current_setting('app.platform_role', true) IN ('admin', 'support')
                OR workspace_id::text = current_setting('app.current_tenant_id', true)
            )
            WITH CHECK (
                current_setting('app.platform_role', true) = 'admin'
                OR workspace_id::text = current_setting('app.current_tenant_id', true)
            );
        """,
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "DROP POLICY IF EXISTS tenant_limit_overrides_isolation "
            "ON tenant_limit_overrides;",
        )
        op.execute(
            "ALTER TABLE tenant_limit_overrides NO FORCE ROW LEVEL SECURITY;",
        )
        op.execute(
            "ALTER TABLE tenant_limit_overrides DISABLE ROW LEVEL SECURITY;",
        )
    op.drop_index(
        "ix_tenant_limit_overrides_workspace_valid_until",
        table_name="tenant_limit_overrides",
    )
    op.drop_table("tenant_limit_overrides")
