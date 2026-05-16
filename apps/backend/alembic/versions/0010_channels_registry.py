"""channels registry + workspace_channels + channel_posts + brands.is_default

Revision ID: 0010_channels_registry
Revises: 0009_tenant_limit_overrides
Create Date: 2026-05-22

PR #14 / docs/plans/phase1-sprint2-plan.md — Channels foundation.

Adds the three tables that hold every connected social channel on
the MVP:

* ``channels`` — Global Channel Registry (D20 in ``docs/03``,
  ``docs/04 §11.3``). One row per ``(platform, external_id)``; NOT
  tenant-scoped because the same channel may be connected from
  multiple workspaces (post-MVP agency feature) and we want one
  ingest pipeline, not N.
* ``workspace_channels`` — many-to-many bridge between a brand and
  a channel. RLS-isolated by ``workspace_id`` (D27 / D65). Carries
  the ``role`` discriminator (``owned`` / ``competitor``) and a
  snapshot of the bot's admin rights.
* ``channel_posts`` — every post we observe on a channel. Monthly
  RANGE partitioning via pg_partman on Postgres so the table stays
  cheap to scan as we accumulate millions of rows; SQLite (tests)
  falls back to a plain table. NOT tenant-scoped — see
  ``channels`` rationale.
* ``brands.is_default`` — exactly one default brand per workspace,
  enforced by a partial unique index. Lets a connect-channel call
  with no explicit brand fall through to the canonical brand.

Additionally registers a pg_cron retention job
``retention_channel_posts_cold_archive`` with ``active = false`` so
the retention policy lives in code from day one; activation happens
together with the rest of the retention jobs in Sprint 8 (D57 in
``docs/04 §18.5``).

Behavior on **SQLite** (test DB, no partitioning extensions): the
``channel_posts`` table is created as a plain table; pg_partman /
pg_cron blocks are skipped. The model layer is dialect-agnostic.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0010_channels_registry"
down_revision: str | None = "0009_tenant_limit_overrides"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _json_type() -> sa.types.TypeEngine[object]:
    """JSONB on Postgres, JSON on SQLite (tests) / other dialects."""

    if _is_postgres():
        return postgresql.JSONB(astext_type=sa.Text())
    return sa.JSON()


# ---------------------------------------------------------------------------
# Postgres-only DDL
# ---------------------------------------------------------------------------

# asyncpg refuses multi-statement prepared statements, so each
# statement here must be ``op.execute(...)`` ed separately.
_PG_CHANNEL_POSTS_PARENT_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE channel_posts (
        id                UUID NOT NULL,
        channel_id        UUID NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
        tg_message_id     BIGINT       NOT NULL,
        text              TEXT,
        entities          JSONB,
        has_media         BOOLEAN      NOT NULL DEFAULT FALSE,
        media_summary     JSONB,
        views_count       INTEGER,
        reactions_count   INTEGER,
        forwards_count    INTEGER,
        posted_at         TIMESTAMPTZ  NOT NULL,
        created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
        CONSTRAINT pk_channel_posts PRIMARY KEY (id, posted_at),
        CONSTRAINT uq_channel_posts_channel_tg_message
            UNIQUE (channel_id, tg_message_id, posted_at)
    ) PARTITION BY RANGE (posted_at)
    """,
    """
    COMMENT ON TABLE channel_posts IS
        'Observed posts on a channel. Monthly RANGE partitions managed '
        'by pg_partman; on fresh installs without pg_partman, an '
        'operator runs partman.create_parent(...). Retention is '
        'enforced by retention_channel_posts_cold_archive pg_cron job '
        '(active=false until Sprint 8 — D57 in docs/04 §18.5).'
    """,
)

_PG_CHANNEL_POSTS_TEMPLATE_STATEMENTS: tuple[str, ...] = (
    "CREATE TABLE channel_posts_template (LIKE channel_posts INCLUDING ALL)",
    "CREATE INDEX IF NOT EXISTS ix_channel_posts_template_channel_posted "
    "ON channel_posts_template (channel_id, posted_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_channel_posts_template_posted "
    "ON channel_posts_template (posted_at DESC)",
)

# Fallback when pg_partman isn't installed: create one bootstrap
# partition for the current month so the parent isn't read-only.
_PG_CHANNEL_POSTS_BOOTSTRAP_PARTITION_DDL = """
DO $bootstrap_channel_posts$
DECLARE
    has_partman BOOLEAN;
    month_start DATE;
    month_end   DATE;
    part_name   TEXT;
BEGIN
    SELECT EXISTS(
        SELECT 1 FROM pg_extension WHERE extname = 'pg_partman'
    ) INTO has_partman;

    IF has_partman THEN
        RETURN;
    END IF;

    month_start := date_trunc('month', now())::date;
    month_end   := (month_start + interval '1 month')::date;
    part_name   := 'channel_posts_p' || to_char(month_start, 'YYYY_MM');

    EXECUTE format(
        'CREATE TABLE IF NOT EXISTS %I PARTITION OF channel_posts '
        'FOR VALUES FROM (%L) TO (%L);',
        part_name, month_start, month_end
    );
END
$bootstrap_channel_posts$;
"""

_PG_CHANNEL_POSTS_PARTMAN_SETUP_DDL = """
DO $partman_channel_posts$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_partman') THEN
        PERFORM partman.create_parent(
            p_parent_table   => 'public.channel_posts',
            p_control        => 'posted_at',
            p_type           => 'range',
            p_interval       => '1 month',
            p_template_table => 'public.channel_posts_template',
            p_premake        => 2
        );
    END IF;
END
$partman_channel_posts$;
"""

# pg_cron retention job — registered as ``active = false`` so it's
# visible in ``cron.job`` from day one but doesn't run yet. Sprint 8
# flips every retention job on together (D57).
_PG_CRON_CHANNEL_POSTS_RETENTION_DDL = """
DO $cron_channel_posts$
DECLARE
    job_id BIGINT;
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
        SELECT cron.schedule(
            'retention_channel_posts_cold_archive',
            '0 5 1 * *',
            $job$
                -- TODO(Sprint 8): DETACH + DROP partitions older than
                -- the retention window after COPY-ing them to cold
                -- storage. No-op while active = false.
                SELECT 1;
            $job$
        ) INTO job_id;
        PERFORM cron.alter_job(job_id, active := false);
    END IF;
END
$cron_channel_posts$;
"""


# ---------------------------------------------------------------------------
# RLS policy on workspace_channels
# ---------------------------------------------------------------------------

# Mirrors the pattern in ``0008_rls_policies.py``. The predicate
# allows admin / support roles to bypass tenant isolation (read-only
# admin lens); inserts / updates still must satisfy the predicate so
# a hijacked admin can't quietly re-parent rows.
_WORKSPACE_CHANNELS_RLS_PREDICATE = (
    "(workspace_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid "
    "OR current_setting('app.platform_role', true) IN ('admin', 'support'))"
)


def upgrade() -> None:
    # 1) brands.is_default + partial unique index.
    op.add_column(
        "brands",
        sa.Column(
            "is_default",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    # Partial unique index: at most one ``is_default=true`` row per
    # workspace. Postgres + SQLite both support partial indexes.
    op.create_index(
        "ux_brands_workspace_default",
        "brands",
        ["workspace_id"],
        unique=True,
        postgresql_where=sa.text("is_default IS TRUE"),
        sqlite_where=sa.text("is_default = 1"),
    )

    # 2) channels — Global Channel Registry, NOT tenant-scoped.
    op.create_table(
        "channels",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("platform", sa.String(length=16), nullable=False),
        sa.Column("external_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("subscribers_count", sa.Integer(), nullable=True),
        sa.Column(
            "is_public",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "platform IN ('telegram')",
            name="ck_channels_platform",
        ),
        sa.UniqueConstraint(
            "platform",
            "external_id",
            name="uq_channels_platform_external",
        ),
    )
    op.create_index(
        "ix_channels_platform_username",
        "channels",
        ["platform", "username"],
    )

    # 3) workspace_channels — bridge with RLS.
    op.create_table(
        "workspace_channels",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "brand_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("brands.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "channel_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channels.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "role",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'owned'"),
        ),
        sa.Column(
            "bot_admin_rights",
            _json_type(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "connected_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "disconnected_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "role IN ('owned', 'competitor')",
            name="ck_workspace_channels_role",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "brand_id",
            "channel_id",
            name="uq_workspace_channels_brand_channel",
        ),
    )
    op.create_index(
        "ix_workspace_channels_brand",
        "workspace_channels",
        ["brand_id"],
    )
    op.create_index(
        "ix_workspace_channels_channel",
        "workspace_channels",
        ["channel_id"],
    )
    # Partial index for the dashboard list-view (active connections).
    op.create_index(
        "ix_workspace_channels_brand_active",
        "workspace_channels",
        ["brand_id"],
        postgresql_where=sa.text("disconnected_at IS NULL"),
        sqlite_where=sa.text("disconnected_at IS NULL"),
    )

    # 4) channel_posts — partitioned on Postgres, flat on SQLite.
    if _is_postgres():
        for stmt in _PG_CHANNEL_POSTS_PARENT_STATEMENTS:
            op.execute(stmt)
        for stmt in _PG_CHANNEL_POSTS_TEMPLATE_STATEMENTS:
            op.execute(stmt)
        op.execute(_PG_CHANNEL_POSTS_BOOTSTRAP_PARTITION_DDL)
        op.execute(_PG_CHANNEL_POSTS_PARTMAN_SETUP_DDL)
        op.execute(_PG_CRON_CHANNEL_POSTS_RETENTION_DDL)
    else:
        op.create_table(
            "channel_posts",
            sa.Column(
                "id",
                postgresql.UUID(as_uuid=True),
                primary_key=True,
                nullable=False,
            ),
            sa.Column(
                "channel_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("channels.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("tg_message_id", sa.BigInteger(), nullable=False),
            sa.Column("text", sa.Text(), nullable=True),
            sa.Column("entities", _json_type(), nullable=True),
            sa.Column(
                "has_media",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column("media_summary", _json_type(), nullable=True),
            sa.Column("views_count", sa.Integer(), nullable=True),
            sa.Column("reactions_count", sa.Integer(), nullable=True),
            sa.Column("forwards_count", sa.Integer(), nullable=True),
            sa.Column(
                "posted_at",
                sa.DateTime(timezone=True),
                nullable=False,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.UniqueConstraint(
                "channel_id",
                "tg_message_id",
                name="uq_channel_posts_channel_tg_message",
            ),
        )
        op.create_index(
            "ix_channel_posts_channel_posted",
            "channel_posts",
            ["channel_id", "posted_at"],
        )

    # 5) RLS policy on workspace_channels (Postgres only).
    if _is_postgres():
        op.execute("ALTER TABLE workspace_channels ENABLE ROW LEVEL SECURITY;")
        op.execute("ALTER TABLE workspace_channels FORCE ROW LEVEL SECURITY;")
        op.execute("DROP POLICY IF EXISTS workspace_channels_isolation ON workspace_channels;")
        op.execute(
            f"""
            CREATE POLICY workspace_channels_isolation ON workspace_channels
                FOR ALL
                TO PUBLIC
                USING ({_WORKSPACE_CHANNELS_RLS_PREDICATE})
                WITH CHECK ({_WORKSPACE_CHANNELS_RLS_PREDICATE});
            """,
        )
        # Grant least-privilege to the app role created in 0008.
        op.execute(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON channels, workspace_channels, "
            "channel_posts TO app_user;"
        )


def downgrade() -> None:
    if _is_postgres():
        op.execute(
            """
            DO $cron_channel_posts_down$
            DECLARE
                job_id BIGINT;
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
                    SELECT jobid INTO job_id FROM cron.job
                        WHERE jobname = 'retention_channel_posts_cold_archive';
                    IF job_id IS NOT NULL THEN
                        PERFORM cron.unschedule(job_id);
                    END IF;
                END IF;
            END
            $cron_channel_posts_down$;
            """,
        )
        op.execute("DROP POLICY IF EXISTS workspace_channels_isolation ON workspace_channels;")
        op.execute("ALTER TABLE workspace_channels NO FORCE ROW LEVEL SECURITY;")
        op.execute("ALTER TABLE workspace_channels DISABLE ROW LEVEL SECURITY;")
        op.execute("DROP TABLE IF EXISTS channel_posts CASCADE;")
        op.execute("DROP TABLE IF EXISTS channel_posts_template CASCADE;")
    else:
        op.drop_index("ix_channel_posts_channel_posted", table_name="channel_posts")
        op.drop_table("channel_posts")

    op.drop_index("ix_workspace_channels_brand_active", table_name="workspace_channels")
    op.drop_index("ix_workspace_channels_channel", table_name="workspace_channels")
    op.drop_index("ix_workspace_channels_brand", table_name="workspace_channels")
    op.drop_table("workspace_channels")

    op.drop_index("ix_channels_platform_username", table_name="channels")
    op.drop_table("channels")

    op.drop_index("ux_brands_workspace_default", table_name="brands")
    op.drop_column("brands", "is_default")
