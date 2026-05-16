"""channel_post_embeddings (pgvector + pg_partman + HNSW + pg_cron safety check)

Revision ID: 0011_channel_post_embeddings
Revises: 0010_channels_registry
Create Date: 2026-05-22

PR #17 / docs/plans/phase1-sprint2-plan.md — Channel-post embeddings
infrastructure.

Adds the table that backs the Brand Memory's long-term vector search
over every observed channel post (docs/04-architecture.md §19.6).

Postgres
--------
* ``CREATE EXTENSION IF NOT EXISTS vector`` (pgvector).
* ``channel_post_embeddings`` parent table, ``PARTITION BY RANGE
  (created_at)`` — partitioning by the *ingest* timestamp keeps
  re-embeddings of historical posts in the current month's partition
  (HNSW indexes are local-per-partition, so this matters for recall).
* Template table ``channel_post_embeddings_template (LIKE … INCLUDING
  ALL)`` with the HNSW index pre-applied, so every partition that
  pg_partman creates inherits the index without an extra ``CREATE
  INDEX`` round-trip.
* HNSW index ``USING hnsw (embedding vector_cosine_ops) WITH
  (m=16, ef_construction=64)`` — cosine ops match the OpenAI /
  text-embedding-3-small semantics; ``m=16`` / ``ef_construction=64``
  are pgvector's documented defaults for "good recall at modest
  memory cost" (docs/04 §19.6 references the same numbers).
* B-tree index on ``(workspace_id, channel_id)`` so the dashboard's
  per-brand vector search can pre-filter by workspace before HNSW
  scoring kicks in.
* ``pg_partman.create_parent`` with monthly partitioning and
  ``p_premake=2`` so two months of partitions are always pre-created
  (covers month-end roll-over without an emergency partman run).
* ``pg_cron.schedule('partman_safety_check', '0 2 25 * *', …)``
  registered with ``active = false`` — a monthly safety net that
  detects "no partition exists for next month" and recreates it.
  Sprint 8 (D57 in docs/04 §18.5) flips every retention / safety job
  on together once the production cron baseline is dialed in.

SQLite (test DB)
----------------
SQLite doesn't ship pgvector / pg_partman / pg_cron, so the test
schema falls back to a *flat* ``channel_post_embeddings`` table with
the same columns. The ``embedding`` column is ``JSON`` — the ORM's
:class:`app.models.channel_post_embedding.Vector` ``TypeDecorator``
serialises the list of floats on write and parses it back on read.
This keeps the unit-test surface dialect-agnostic; vector search
itself is exercised in Sprint 3 against Postgres.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0011_channel_post_embeddings"
down_revision: str | None = "0010_channels_registry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---- Embedding dimensionality ----
# docs/04-architecture.md §19.6 + ``app.core.config.Settings``: MVP
# uses ``text-embedding-3-small`` at 1536 dims. The migration mirrors
# the application default so a fresh dev / CI environment is wired
# end-to-end without an extra env override.
EMBEDDING_DIM = 1536


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


# ---------------------------------------------------------------------------
# Postgres-only DDL
# ---------------------------------------------------------------------------

# pgvector extension — idempotent, so re-running the migration after a
# manual ``DROP EXTENSION`` doesn't leave the DDL half-applied.
_PG_VECTOR_EXTENSION_DDL = "CREATE EXTENSION IF NOT EXISTS vector;"


# Parent table. The composite primary key includes the partition key
# (``created_at``) — Postgres requires every UNIQUE / PRIMARY KEY on a
# partitioned table to embed the partition key. ``UNIQUE (channel_post_id,
# model, created_at)`` is the idempotency anchor: re-embedding the same
# (post, model) updates the existing row instead of creating a duplicate.
_PG_PARENT_STATEMENTS: tuple[str, ...] = (
    f"""
    CREATE TABLE channel_post_embeddings (
        id                UUID NOT NULL,
        channel_post_id   UUID NOT NULL,
        channel_id        UUID NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
        workspace_id      UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        model             VARCHAR(64) NOT NULL,
        embedding         vector({EMBEDDING_DIM}) NOT NULL,
        created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT pk_channel_post_embeddings
            PRIMARY KEY (id, created_at),
        CONSTRAINT uq_channel_post_embeddings_post_model
            UNIQUE (channel_post_id, model, created_at)
    ) PARTITION BY RANGE (created_at)
    """,
    """
    COMMENT ON TABLE channel_post_embeddings IS
        'Vector embeddings of channel_posts.text + caption (one row '
        'per (channel_post_id, model)). Partitioned monthly by '
        'created_at via pg_partman; HNSW index lives on the template '
        'so new partitions inherit it automatically. Retention + cold '
        'archive land in Sprint 8 (D57 in docs/04 §18.5).'
    """,
)

# Template table — LIKE … INCLUDING ALL copies columns + constraints +
# the partition-aware PK / unique constraints. pgvector indexes don't
# come along with INCLUDING ALL, so we ``CREATE INDEX`` after.
#
# B-tree on (workspace_id, channel_id) is the cheap pre-filter:
# dashboard "search inside my brand only" scans this index, then HNSW
# scores the residual rows. The HNSW index uses cosine ops to match
# the OpenAI embedding norm.
_PG_TEMPLATE_STATEMENTS: tuple[str, ...] = (
    "CREATE TABLE channel_post_embeddings_template "
    "(LIKE channel_post_embeddings INCLUDING ALL)",
    # The HNSW index must live on the template so pg_partman copies it
    # onto every new partition; an index on the parent of a partitioned
    # table only covers existing partitions, not future ones.
    "CREATE INDEX IF NOT EXISTS ix_channel_post_embeddings_template_hnsw "
    "ON channel_post_embeddings_template "
    "USING hnsw (embedding vector_cosine_ops) "
    "WITH (m = 16, ef_construction = 64)",
    "CREATE INDEX IF NOT EXISTS ix_channel_post_embeddings_template_ws_channel "
    "ON channel_post_embeddings_template (workspace_id, channel_id)",
    "CREATE INDEX IF NOT EXISTS ix_channel_post_embeddings_template_post "
    "ON channel_post_embeddings_template (channel_post_id)",
)

# Fallback bootstrap partition when pg_partman isn't installed — keeps
# the parent table writable so dev / CI without the extension still
# work end-to-end (the production deployment installs partman as part
# of the Postgres image).
_PG_BOOTSTRAP_PARTITION_DDL = """
DO $bootstrap_channel_post_embeddings$
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
    part_name   := 'channel_post_embeddings_p' || to_char(month_start, 'YYYY_MM');

    EXECUTE format(
        'CREATE TABLE IF NOT EXISTS %I PARTITION OF channel_post_embeddings '
        'FOR VALUES FROM (%L) TO (%L);',
        part_name, month_start, month_end
    );
END
$bootstrap_channel_post_embeddings$;
"""

# pg_partman registration — monthly RANGE on ``created_at`` with two
# pre-made partitions so month-end roll-overs never block an INSERT.
_PG_PARTMAN_SETUP_DDL = """
DO $partman_channel_post_embeddings$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_partman') THEN
        PERFORM partman.create_parent(
            p_parent_table   => 'public.channel_post_embeddings',
            p_control        => 'created_at',
            p_type           => 'range',
            p_interval       => '1 month',
            p_template_table => 'public.channel_post_embeddings_template',
            p_premake        => 2
        );
    END IF;
END
$partman_channel_post_embeddings$;
"""

# pg_cron monthly safety check — runs at 02:00 UTC on the 25th and
# logs / fixes a missing partition for the next month. Registered as
# ``active = false`` so the job is visible from day one but doesn't
# fire until Sprint 8 (D57 in docs/04 §18.5).
_PG_CRON_PARTMAN_SAFETY_DDL = """
DO $cron_partman_safety_channel_post_embeddings$
DECLARE
    job_id BIGINT;
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
        SELECT cron.schedule(
            'partman_safety_check_channel_post_embeddings',
            '0 2 25 * *',
            $job$
                -- TODO(Sprint 8): assert that a partition exists for
                -- ``date_trunc('month', now() + interval '1 month')``
                -- and call partman.run_maintenance() if not. No-op
                -- while active = false so the cron.job row is just
                -- the contract.
                SELECT 1;
            $job$
        ) INTO job_id;
        PERFORM cron.alter_job(job_id, active := false);
    END IF;
END
$cron_partman_safety_channel_post_embeddings$;
"""


# ---------------------------------------------------------------------------
# upgrade / downgrade
# ---------------------------------------------------------------------------


def upgrade() -> None:
    if _is_postgres():
        # 1) Extension. ``IF NOT EXISTS`` so a manually-installed
        # pgvector doesn't fail the migration on re-run.
        op.execute(_PG_VECTOR_EXTENSION_DDL)

        # 2) Parent + template tables.
        for stmt in _PG_PARENT_STATEMENTS:
            op.execute(stmt)
        for stmt in _PG_TEMPLATE_STATEMENTS:
            op.execute(stmt)

        # 3) Bootstrap a partition when partman is missing so the
        # parent isn't read-only on a fresh install.
        op.execute(_PG_BOOTSTRAP_PARTITION_DDL)

        # 4) Register pg_partman + pg_cron safety check.
        op.execute(_PG_PARTMAN_SETUP_DDL)
        op.execute(_PG_CRON_PARTMAN_SAFETY_DDL)

        # 5) Grant least-privilege to the app role created in 0008.
        op.execute(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON channel_post_embeddings TO app_user;"
        )
        return

    # SQLite (tests): flat table with JSON-serialised embedding.
    op.create_table(
        "channel_post_embeddings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "channel_post_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "channel_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channels.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("embedding", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint(
            "channel_post_id",
            "model",
            name="uq_channel_post_embeddings_post_model",
        ),
    )
    op.create_index(
        "ix_channel_post_embeddings_ws_channel",
        "channel_post_embeddings",
        ["workspace_id", "channel_id"],
    )
    op.create_index(
        "ix_channel_post_embeddings_post",
        "channel_post_embeddings",
        ["channel_post_id"],
    )


def downgrade() -> None:
    if _is_postgres():
        op.execute(
            """
            DO $cron_safety_down_channel_post_embeddings$
            DECLARE
                job_id BIGINT;
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
                    SELECT jobid INTO job_id FROM cron.job
                        WHERE jobname = 'partman_safety_check_channel_post_embeddings';
                    IF job_id IS NOT NULL THEN
                        PERFORM cron.unschedule(job_id);
                    END IF;
                END IF;
            END
            $cron_safety_down_channel_post_embeddings$;
            """,
        )
        op.execute("DROP TABLE IF EXISTS channel_post_embeddings CASCADE;")
        op.execute("DROP TABLE IF EXISTS channel_post_embeddings_template CASCADE;")
        # ``DROP EXTENSION vector`` is intentionally NOT issued: the
        # extension may be shared with other tables in Sprint 3+ (Brand
        # Memory note embeddings) and a downgrade shouldn't take them
        # down too.
        return

    op.drop_index("ix_channel_post_embeddings_post", table_name="channel_post_embeddings")
    op.drop_index(
        "ix_channel_post_embeddings_ws_channel",
        table_name="channel_post_embeddings",
    )
    op.drop_table("channel_post_embeddings")
