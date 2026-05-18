"""brand_memory_core / brand_memory_overlays / brand_memory_examples (+ RLS, partitioning)

Revision ID: 0015_brand_memory
Revises: 0014_agent_audit_log
Create Date: 2026-05-18

PR #21 / docs/plans/phase1-sprint3-plan.md — Brand Memory schema.

The two-layer Brand Memory model (П11 single source of truth) consists of
three tables:

* ``brand_memory_core`` — exactly one row per brand. ``payload`` is the
  canonical JSONB blob holding tone-of-voice, target audience, taboos,
  post types, post frequency, etc. (docs/03 §D24, EPIC-K). Optimistic
  concurrency via ``version`` so a stale PATCH from the SPA can be
  rejected client-side; ``updated_by_user_id`` / ``updated_by_agent``
  attribute the last change to the responsible actor (manual edit vs.
  OnboardingAgent extraction in PR #22).
* ``brand_memory_overlays`` — per-channel overrides keyed on
  ``(brand_id, workspace_channel_id)``. Sparse: the Brand Memory service
  treats a missing row as "no overlay → fall back to core". Same shape
  as ``brand_memory_core`` payload + version + attribution.
* ``brand_memory_examples`` — vector embeddings of representative brand
  posts used by ``search_examples()`` for similarity-anchored content
  drafting. Mirrors ``channel_post_embeddings`` from PR #17: pgvector
  with HNSW + monthly ``pg_partman`` partitioning on ``created_at``.

Postgres-only
-------------
* RLS policies (``workspace_id = current_tenant_id`` OR
  ``platform_role IN ('admin', 'support')``) on all three tables;
  identical predicate to the audit-log policies from PR #20 so a
  cross-tenant probe surfaces the same 403 regardless of which table
  is queried.
* ``brand_memory_examples`` lives behind the same pgvector / pg_partman
  / HNSW infrastructure as ``channel_post_embeddings``: parent table
  with composite PK ``(id, created_at)``, a template table that carries
  the HNSW + B-tree indexes so pg_partman copies them onto each new
  monthly partition, and a fallback bootstrap partition for environments
  that don't have pg_partman installed (dev / CI).
* ``GRANT`` SELECT/INSERT/UPDATE/DELETE to ``app_user`` so the runtime
  role can read+write under RLS.

SQLite (test DB)
----------------
SQLite has no pgvector / RLS / pg_partman, so the test schema uses
flat tables with ``JSON`` for the embedding column. The dialect-aware
``Vector`` TypeDecorator in :mod:`app.models.brand_memory` serialises
the list of floats on write and rebuilds it on read.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0015_brand_memory"
down_revision: str | None = "0014_agent_audit_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Mirrors :data:`app.models.channel_post_embedding.EMBEDDING_DIM` and the
# 0011 migration. Keeping the constant local to the migration so a future
# bump of the embedding model doesn't have to chase an import through
# the ORM tree.
EMBEDDING_DIM = 1536


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


# ---------------------------------------------------------------------------
# Postgres DDL
# ---------------------------------------------------------------------------


_PG_CORE_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS brand_memory_core (
        id                    UUID PRIMARY KEY,
        workspace_id          UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        brand_id              UUID NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
        payload               JSONB NOT NULL DEFAULT '{}'::jsonb,
        version               INTEGER NOT NULL DEFAULT 1,
        updated_by_user_id    UUID NULL REFERENCES users(id) ON DELETE SET NULL,
        updated_by_agent      VARCHAR(64) NULL,
        created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_brand_memory_core_brand UNIQUE (brand_id)
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_brand_memory_core_workspace
        ON brand_memory_core (workspace_id);
    """,
    """
    COMMENT ON TABLE brand_memory_core IS
        'Single-source-of-truth tone / audience / taboos payload per brand. '
        'PR #21; payload schema validated by app.schemas.brand_memory.';
    """,
)


_PG_OVERLAY_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS brand_memory_overlays (
        id                       UUID PRIMARY KEY,
        workspace_id             UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        brand_id                 UUID NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
        workspace_channel_id     UUID NOT NULL REFERENCES workspace_channels(id) ON DELETE CASCADE,
        payload                  JSONB NOT NULL DEFAULT '{}'::jsonb,
        version                  INTEGER NOT NULL DEFAULT 1,
        updated_by_user_id       UUID NULL REFERENCES users(id) ON DELETE SET NULL,
        updated_by_agent         VARCHAR(64) NULL,
        created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_brand_memory_overlays_brand_channel
            UNIQUE (brand_id, workspace_channel_id)
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_brand_memory_overlays_workspace
        ON brand_memory_overlays (workspace_id);
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_brand_memory_overlays_brand
        ON brand_memory_overlays (brand_id);
    """,
    """
    COMMENT ON TABLE brand_memory_overlays IS
        'Per-channel overrides applied on top of brand_memory_core. PR #21.';
    """,
)


# Parent partitioned table. The composite primary key embeds the partition
# key ``created_at`` because Postgres requires every UNIQUE / PRIMARY KEY
# on a partitioned table to include the partition column.
_PG_EXAMPLES_PARENT_STATEMENTS: tuple[str, ...] = (
    f"""
    CREATE TABLE IF NOT EXISTS brand_memory_examples (
        id                          UUID NOT NULL,
        workspace_id                UUID NOT NULL,
        brand_id                    UUID NOT NULL,
        source_channel_post_id      UUID NULL,
        model                       VARCHAR(64) NOT NULL,
        text_snippet                TEXT NOT NULL,
        embedding                   vector({EMBEDDING_DIM}) NOT NULL,
        created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT pk_brand_memory_examples
            PRIMARY KEY (id, created_at)
    ) PARTITION BY RANGE (created_at);
    """,
    """
    COMMENT ON TABLE brand_memory_examples IS
        'Vector-indexed brand-post examples. Used by BrandMemoryService.search_examples(). '
        'Partitioned monthly via pg_partman; HNSW lives on the template. PR #21.';
    """,
)


# Template table — CREATE LIKE … INCLUDING ALL copies columns + constraints
# + the partition-aware PK. pgvector / B-tree indexes are added afterwards
# so pg_partman copies them onto every new partition automatically.
_PG_EXAMPLES_TEMPLATE_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS brand_memory_examples_template
        (LIKE brand_memory_examples INCLUDING ALL);
    """,
    "CREATE INDEX IF NOT EXISTS ix_brand_memory_examples_template_hnsw "
    "ON brand_memory_examples_template "
    "USING hnsw (embedding vector_cosine_ops) "
    "WITH (m = 16, ef_construction = 64);",
    "CREATE INDEX IF NOT EXISTS ix_brand_memory_examples_template_ws_brand "
    "ON brand_memory_examples_template (workspace_id, brand_id);",
    "CREATE INDEX IF NOT EXISTS ix_brand_memory_examples_template_brand_created "
    "ON brand_memory_examples_template (brand_id, created_at DESC);",
)


_PG_EXAMPLES_BOOTSTRAP_DDL = """
DO $bootstrap_brand_memory_examples$
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
    part_name   := 'brand_memory_examples_p' || to_char(month_start, 'YYYY_MM');

    EXECUTE format(
        'CREATE TABLE IF NOT EXISTS %I PARTITION OF brand_memory_examples '
        'FOR VALUES FROM (%L) TO (%L);',
        part_name, month_start, month_end
    );
END
$bootstrap_brand_memory_examples$;
"""


_PG_EXAMPLES_PARTMAN_DDL = """
DO $partman_brand_memory_examples$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_partman') THEN
        PERFORM partman.create_parent(
            p_parent_table   => 'public.brand_memory_examples',
            p_control        => 'created_at',
            p_type           => 'range',
            p_interval       => '1 month',
            p_template_table => 'public.brand_memory_examples_template',
            p_premake        => 2
        );
    END IF;
END
$partman_brand_memory_examples$;
"""


# ---------------------------------------------------------------------------
# RLS policies (Postgres-only)
# ---------------------------------------------------------------------------

_RLS_TABLES: tuple[tuple[str, str], ...] = (
    (
        "brand_memory_core",
        (
            "(workspace_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid "
            "OR current_setting('app.platform_role', true) IN ('admin', 'support'))"
        ),
    ),
    (
        "brand_memory_overlays",
        (
            "(workspace_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid "
            "OR current_setting('app.platform_role', true) IN ('admin', 'support'))"
        ),
    ),
    (
        "brand_memory_examples",
        (
            "(workspace_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid "
            "OR current_setting('app.platform_role', true) IN ('admin', 'support'))"
        ),
    ),
)


# ---------------------------------------------------------------------------
# upgrade / downgrade
# ---------------------------------------------------------------------------


def upgrade() -> None:
    if _is_postgres():
        # pgvector is already installed by migration 0011; we still issue
        # IF NOT EXISTS so a fresh-DB run that somehow skipped 0011 keeps
        # the column type resolvable.
        op.execute("CREATE EXTENSION IF NOT EXISTS vector;")

        # 1) brand_memory_core + brand_memory_overlays (regular tables).
        for stmt in _PG_CORE_STATEMENTS:
            op.execute(stmt)
        for stmt in _PG_OVERLAY_STATEMENTS:
            op.execute(stmt)

        # 2) brand_memory_examples — partitioned parent + template.
        for stmt in _PG_EXAMPLES_PARENT_STATEMENTS:
            op.execute(stmt)
        for stmt in _PG_EXAMPLES_TEMPLATE_STATEMENTS:
            op.execute(stmt)
        op.execute(_PG_EXAMPLES_BOOTSTRAP_DDL)
        op.execute(_PG_EXAMPLES_PARTMAN_DDL)

        # 3) RLS policies. Identical predicate / shape to migration 0014.
        for table, predicate in _RLS_TABLES:
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
            op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
            op.execute(f"DROP POLICY IF EXISTS {table}_isolation ON {table};")
            op.execute(
                f"""
                CREATE POLICY {table}_isolation ON {table}
                    FOR ALL
                    TO PUBLIC
                    USING ({predicate})
                    WITH CHECK ({predicate});
                """,
            )

        # 4) Least-privilege grants to the runtime role.
        op.execute(
            """
            GRANT SELECT, INSERT, UPDATE, DELETE ON
                brand_memory_core, brand_memory_overlays, brand_memory_examples
                TO app_user;
            """,
        )
        return

    # SQLite (tests): flat tables with JSON-serialised embedding.
    op.create_table(
        "brand_memory_core",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
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
            "payload",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column(
            "updated_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("updated_by_agent", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint("brand_id", name="uq_brand_memory_core_brand"),
    )
    op.create_index(
        "ix_brand_memory_core_workspace",
        "brand_memory_core",
        ["workspace_id"],
    )

    op.create_table(
        "brand_memory_overlays",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
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
            "workspace_channel_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspace_channels.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "payload",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column(
            "updated_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("updated_by_agent", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint(
            "brand_id",
            "workspace_channel_id",
            name="uq_brand_memory_overlays_brand_channel",
        ),
    )
    op.create_index(
        "ix_brand_memory_overlays_workspace",
        "brand_memory_overlays",
        ["workspace_id"],
    )
    op.create_index(
        "ix_brand_memory_overlays_brand",
        "brand_memory_overlays",
        ["brand_id"],
    )

    op.create_table(
        "brand_memory_examples",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
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
            "source_channel_post_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("text_snippet", sa.Text(), nullable=False),
        sa.Column("embedding", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "ix_brand_memory_examples_ws_brand",
        "brand_memory_examples",
        ["workspace_id", "brand_id"],
    )
    op.create_index(
        "ix_brand_memory_examples_brand_created",
        "brand_memory_examples",
        ["brand_id", "created_at"],
    )


def downgrade() -> None:
    if _is_postgres():
        for table, _ in _RLS_TABLES:
            op.execute(f"DROP POLICY IF EXISTS {table}_isolation ON {table};")
            op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")

        op.execute("DROP TABLE IF EXISTS brand_memory_examples CASCADE;")
        op.execute("DROP TABLE IF EXISTS brand_memory_examples_template CASCADE;")
        op.execute("DROP TABLE IF EXISTS brand_memory_overlays CASCADE;")
        op.execute("DROP TABLE IF EXISTS brand_memory_core CASCADE;")
        # ``DROP EXTENSION vector`` is intentionally NOT issued — other
        # Sprint-2/3 tables (channel_post_embeddings) keep using it.
        return

    op.drop_index(
        "ix_brand_memory_examples_brand_created",
        table_name="brand_memory_examples",
    )
    op.drop_index(
        "ix_brand_memory_examples_ws_brand",
        table_name="brand_memory_examples",
    )
    op.drop_table("brand_memory_examples")

    op.drop_index(
        "ix_brand_memory_overlays_brand",
        table_name="brand_memory_overlays",
    )
    op.drop_index(
        "ix_brand_memory_overlays_workspace",
        table_name="brand_memory_overlays",
    )
    op.drop_table("brand_memory_overlays")

    op.drop_index(
        "ix_brand_memory_core_workspace",
        table_name="brand_memory_core",
    )
    op.drop_table("brand_memory_core")
