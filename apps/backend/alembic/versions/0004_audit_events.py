"""audit_events (partitioned + retention pg_cron skeleton).

Revision ID: 0004_audit_events
Revises: 0003_user_totp
Create Date: 2026-05-15

PR #5 / phase0-phase1-sprint1-plan.md.

Implements the ``audit_events`` table per
docs/04-architecture.md §10.1 schema + §11 indexes:

    audit_events
      id (UUID PK)
      user_id (FK users, nullable)
      workspace_id (FK workspaces, nullable)
      event_type           — 'user.login_success', 'user.password_changed', ...
      severity ENUM('info','warning','critical')
      ip_address, user_agent, metadata JSONB,
      created_at
    Indexes:
      (user_id, created_at DESC)
      (workspace_id, severity, created_at DESC) WHERE severity = 'critical'
      (event_type, created_at DESC)

On **Postgres** the parent table is declared ``PARTITION BY RANGE
(created_at)``. We then:

* Create a template table ``audit_events_template`` with the same
  shape + indexes — pg_partman copies these onto every new partition
  it builds.
* Call ``partman.create_parent(..., p_template_table => ...,
  p_premake => 2, p_interval => 'monthly')`` so a partition for the
  current month + two future months exists out of the gate.
* Create the retention pg_cron job
  ``retention_audit_log_cold_archive`` with ``schedule='0 5 1 * *'``
  (5 a.m. on the 1st of each month, UTC) and **flip it to
  ``active = false``** — per docs/06-roadmap.md §5 Сприннт 1 +
  D57: the policy is in place but every retention sweep is paused
  until Sprint 8 turns them all on together.

On **SQLite** (the test DB, no partitioning extensions) the same
migration falls back to a single plain table — the model layer
doesn't care which one it talks to. ``pg_partman`` / ``pg_cron``
blocks are dialect-guarded.

The migration is also tolerant of Postgres setups without
``pg_partman`` / ``pg_cron`` installed (CI, fresh local dev):
extension-dependent SQL is wrapped in DO blocks that skip when
``pg_extension`` rows for ``pg_partman`` / ``pg_cron`` aren't
present, and the parent table degrades to ``PARTITION BY RANGE``
with one explicit partition the migration creates itself. The full
pg_partman setup is then a one-liner the operator runs once
(``SELECT partman.create_parent(...)``) — documented in the table
comment.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0004_audit_events"
down_revision: str | None = "0003_user_totp"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Postgres DDL
# ---------------------------------------------------------------------------

# asyncpg refuses multi-statement prepared statements, so each
# statement here must be ``op.execute(...)`` ed separately.
_PG_PARENT_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE audit_events (
        id           UUID NOT NULL,
        user_id      UUID         REFERENCES users(id)      ON DELETE SET NULL,
        workspace_id UUID         REFERENCES workspaces(id) ON DELETE SET NULL,
        event_type   VARCHAR(64)  NOT NULL,
        severity     VARCHAR(16)  NOT NULL,
        ip_address   INET,
        user_agent   TEXT,
        metadata     JSONB        NOT NULL DEFAULT '{}'::jsonb,
        created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
        CONSTRAINT pk_audit_events PRIMARY KEY (id, created_at),
        CONSTRAINT ck_audit_events_severity
            CHECK (severity IN ('info', 'warning', 'critical'))
    ) PARTITION BY RANGE (created_at)
    """,
    """
    COMMENT ON TABLE audit_events IS
        'Sensitive-ops audit log. Monthly RANGE partitions; managed by '
        'pg_partman if available, else operator runs '
        'partman.create_parent(...). Retention: hot 2y + cold 5y (D57).'
    """,
)

_PG_TEMPLATE_STATEMENTS: tuple[str, ...] = (
    "CREATE TABLE audit_events_template (LIKE audit_events INCLUDING ALL)",
    "CREATE INDEX IF NOT EXISTS ix_audit_events_template_user_id_created_at "
    "ON audit_events_template (user_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_audit_events_template_event_type_created_at "
    "ON audit_events_template (event_type, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_audit_events_template_workspace_critical "
    "ON audit_events_template (workspace_id, severity, created_at DESC) "
    "WHERE severity = 'critical'",
)

# Fallback when pg_partman isn't installed: create one bootstrap
# partition for the current month so the parent isn't read-only.
# Operators set up pg_partman after-the-fact with a single
# ``partman.create_parent`` call.
_PG_BOOTSTRAP_PARTITION_DDL = """
DO $bootstrap$
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
        -- pg_partman will manage partitions; create_parent must
        -- still be invoked by the operator once because it needs
        -- the ``partman`` schema and we don't want this migration
        -- to require superuser. Skip the manual partition here.
        RETURN;
    END IF;

    month_start := date_trunc('month', now())::date;
    month_end   := (month_start + interval '1 month')::date;
    part_name   := 'audit_events_p' || to_char(month_start, 'YYYY_MM');

    EXECUTE format(
        'CREATE TABLE IF NOT EXISTS %I PARTITION OF audit_events '
        'FOR VALUES FROM (%L) TO (%L);',
        part_name, month_start, month_end
    );
END
$bootstrap$;
"""

# pg_partman setup (optional — runs only when the extension is
# present). p_premake=2 so the current + 2 future months exist
# immediately. p_template_table copies our indexes onto every new
# partition automatically (D61 pattern, re-applied to audit_events).
_PG_PARTMAN_SETUP_DDL = """
DO $partman$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_partman') THEN
        PERFORM partman.create_parent(
            p_parent_table   => 'public.audit_events',
            p_control        => 'created_at',
            p_type           => 'range',
            p_interval       => '1 month',
            p_template_table => 'public.audit_events_template',
            p_premake        => 2
        );
    END IF;
END
$partman$;
"""

# pg_cron retention job (active = false). The job is registered so
# the operations team can see it in ``cron.job`` and flip it on with
# a single ``cron.alter_job(job_id, active := true)`` in Sprint 8.
_PG_CRON_SETUP_DDL = """
DO $cron$
DECLARE
    job_id BIGINT;
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
        SELECT cron.schedule(
            'retention_audit_log_cold_archive',
            '0 5 1 * *',
            $job$
                -- TODO(Sprint 8): COPY partitions older than 24 months to S3
                -- then DETACH + DROP. Body intentionally a no-op SELECT
                -- while ``active = false``.
                SELECT 1;
            $job$
        ) INTO job_id;
        PERFORM cron.alter_job(job_id, active := false);
    END IF;
END
$cron$;
"""


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _json_type() -> sa.types.TypeEngine[object]:
    """JSONB on Postgres, JSON on SQLite (tests) / other dialects."""

    if _is_postgres():
        return postgresql.JSONB(astext_type=sa.Text())
    return sa.JSON()


def upgrade() -> None:
    if _is_postgres():
        for stmt in _PG_PARENT_STATEMENTS:
            op.execute(stmt)
        for stmt in _PG_TEMPLATE_STATEMENTS:
            op.execute(stmt)
        op.execute(_PG_BOOTSTRAP_PARTITION_DDL)
        op.execute(_PG_PARTMAN_SETUP_DDL)
        op.execute(_PG_CRON_SETUP_DDL)
        return

    # SQLite (tests) — single flat table, no partitioning. The ORM
    # talks to the same logical schema; partition-management lives
    # in Postgres-only DDL above.
    op.create_table(
        "audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column(
            "metadata",
            _json_type(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "severity IN ('info', 'warning', 'critical')",
            name="ck_audit_events_severity",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_events"),
    )
    op.create_index(
        "ix_audit_events_user_id_created_at",
        "audit_events",
        ["user_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_audit_events_event_type_created_at",
        "audit_events",
        ["event_type", sa.text("created_at DESC")],
    )
    # Partial index on the critical-severity slice. SQLite supports
    # partial indexes; on Postgres the equivalent lives on the
    # template (and thus on every partition) above.
    op.create_index(
        "ix_audit_events_workspace_critical",
        "audit_events",
        ["workspace_id", "severity", sa.text("created_at DESC")],
        sqlite_where=sa.text("severity = 'critical'"),
    )


def downgrade() -> None:
    if _is_postgres():
        op.execute(
            "DO $cron$ BEGIN "
            "IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN "
            "  PERFORM cron.unschedule('retention_audit_log_cold_archive'); "
            "END IF; END $cron$;"
        )
        op.execute(
            "DO $partman$ BEGIN "
            "IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_partman') THEN "
            "  PERFORM partman.undo_partition("
            "    p_parent_table => 'public.audit_events',"
            "    p_keep_table => false"
            "  ); "
            "END IF; END $partman$;"
        )
        op.execute("DROP TABLE IF EXISTS audit_events CASCADE;")
        op.execute("DROP TABLE IF EXISTS audit_events_template CASCADE;")
        return

    op.drop_index("ix_audit_events_workspace_critical", table_name="audit_events")
    op.drop_index("ix_audit_events_event_type_created_at", table_name="audit_events")
    op.drop_index("ix_audit_events_user_id_created_at", table_name="audit_events")
    op.drop_table("audit_events")
