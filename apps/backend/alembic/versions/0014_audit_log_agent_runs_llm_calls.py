"""audit log: agent_runs / llm_calls / llm_calls_daily + fx_rates + opt_in_training

Revision ID: 0014_agent_audit_log
Revises: 0013_seed_plan_catalog
Create Date: 2026-05-17

PR #20 / docs/plans/phase1-sprint3-plan.md — Audit log foundation.

Creates the four tables every agent depends on for traceability +
cost accounting:

* ``agent_runs`` — one row per agent invocation. Carries the
  denormalised totals (``prompt_tokens`` / ``completion_tokens``
  / ``cost_usd`` / ``cost_rub`` / ``latency_ms``), the agent's
  ``chain_of_thought`` (only when the user opted in to training
  data), the linked ``parent_run_id`` for orchestrated multi-agent
  flows, and a ``status`` (``started`` / ``succeeded`` / ``failed``).
* ``llm_calls`` — one row per provider HTTP round-trip. One
  ``agent_run`` typically fires N llm-calls (tool-calling loops).
  Carries the per-call cost split (``input_cost_usd`` /
  ``output_cost_usd``) + circuit-breaker state + retry count so
  the admin dashboard can surface "this run cost $0.42 across 7
  calls; 2 retries; breaker stayed CLOSED".
* ``llm_calls_daily`` — monthly-partitioned aggregate (one row per
  ``(workspace, brand, date, provider, model)``). Sprint 8's
  retention job rolls ``llm_calls`` into this table at 90 days
  and drops the raw rows.
* ``fx_rates`` — USD → RUB snapshot history. The agent run writer
  reads the latest row at ``finish_run`` time to compute
  ``cost_rub`` from ``cost_usd``.

Also adds ``users.opt_in_training`` (default ``false``) — the
per-user consent toggle for "use my prompts / completions to
improve the model" (D67 in docs/04 §18.5). Snapshotted onto every
``agent_runs`` / ``llm_calls`` row at write time so a later
policy flip can't retroactively re-enable retention zeroing of
already-completed runs.

Postgres-only DDL
-----------------
* ``llm_calls_daily`` is ``PARTITION BY RANGE (date)`` with a
  template table + pg_partman monthly registration (``p_premake=2``)
  mirroring PR #17's :mod:`channel_post_embeddings` pattern.
* RLS policies (``workspace_id = current_tenant_id`` OR
  ``platform_role IN ('admin', 'support')``) on all three audit
  tables — column-level redaction (e.g. ``chain_of_thought`` for
  ``support``) lives in the API serialiser, not the policy.
* Two ``pg_cron`` jobs registered with ``active = false``:

  - ``retention_chain_of_thought`` — daily 03:00 UTC, calls the
    ``retention_chain_of_thought_run()`` pg function which zeros
    ``agent_runs.chain_of_thought`` for runs older than 30 days
    that didn't opt in.
  - ``retention_llm_calls_aggregate`` — daily 03:30 UTC, calls
    ``retention_llm_calls_aggregate_run()`` which folds raw
    ``llm_calls`` older than 90 days into ``llm_calls_daily`` and
    deletes them.

  Both pg functions are installed by this migration. Activation of
  the cron jobs themselves is deferred to Sprint 8 (D57 in docs/04
  §18.5) — when the whole retention quintet flips at once.

SQLite (test DB)
----------------
SQLite has no pg_partman / pg_cron / RLS, so the test schema gets
the same tables in flat form with ``JSON`` instead of ``JSONB``.
RLS tests are marked ``@pytest.mark.postgres``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0014_agent_audit_log"
down_revision: str | None = "0013_seed_plan_catalog"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


# ---------------------------------------------------------------------------
# Postgres DDL
# ---------------------------------------------------------------------------

_PG_ADD_OPT_IN_TRAINING_DDL = """
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS opt_in_training BOOLEAN
        NOT NULL DEFAULT false;
"""


# ``asyncpg`` rejects multi-statement strings inside a single prepared
# statement, so DDL blocks that bundle a ``CREATE TABLE`` with one or more
# ``CREATE INDEX`` calls are kept as tuples and executed one statement at
# a time (same pattern as PR #17 / migration 0011).
_PG_FX_RATES_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS fx_rates (
        id              UUID PRIMARY KEY,
        base_currency   VARCHAR(8) NOT NULL,
        quote_currency  VARCHAR(8) NOT NULL,
        rate            NUMERIC(18, 8) NOT NULL,
        observed_at     TIMESTAMPTZ NOT NULL,
        source          VARCHAR(32) NOT NULL DEFAULT 'manual',
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_fx_rates_natural UNIQUE (base_currency, quote_currency, observed_at)
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_fx_rates_pair_observed
        ON fx_rates (base_currency, quote_currency, observed_at DESC);
    """,
)


_PG_AGENT_RUNS_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS agent_runs (
        id                    UUID PRIMARY KEY,
        workspace_id          UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        brand_id              UUID NULL REFERENCES brands(id) ON DELETE SET NULL,
        agent                 VARCHAR(64) NOT NULL,
        agent_version         VARCHAR(32) NOT NULL DEFAULT 'v0',
        status                VARCHAR(16) NOT NULL,
        started_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
        finished_at           TIMESTAMPTZ NULL,
        latency_ms            BIGINT NULL,
        prompt_tokens         INTEGER NOT NULL DEFAULT 0,
        completion_tokens     INTEGER NOT NULL DEFAULT 0,
        cost_usd              NUMERIC(14, 6) NOT NULL DEFAULT 0,
        cost_rub              NUMERIC(14, 4) NOT NULL DEFAULT 0,
        error_code            VARCHAR(64) NULL,
        error_message         TEXT NULL,
        chain_of_thought      JSONB NULL,
        retrieved_context     JSONB NULL,
        skills_used           JSONB NOT NULL DEFAULT '[]'::jsonb,
        parent_run_id         UUID NULL REFERENCES agent_runs(id) ON DELETE SET NULL,
        idempotency_key       TEXT NULL,
        originator_user_id    UUID NULL REFERENCES users(id) ON DELETE SET NULL,
        opt_in_training       BOOLEAN NOT NULL DEFAULT false,
        created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT ck_agent_runs_status
            CHECK (status IN ('started', 'succeeded', 'failed', 'cancelled'))
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_agent_runs_workspace_started
        ON agent_runs (workspace_id, started_at DESC);
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_agent_runs_brand_started
        ON agent_runs (brand_id, started_at DESC);
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_agent_runs_agent
        ON agent_runs (agent, started_at DESC);
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_agent_runs_parent
        ON agent_runs (parent_run_id)
        WHERE parent_run_id IS NOT NULL;
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_agent_runs_cot_gin
        ON agent_runs USING gin (chain_of_thought)
        WHERE chain_of_thought IS NOT NULL;
    """,
)


_PG_LLM_CALLS_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS llm_calls (
        id                      UUID PRIMARY KEY,
        agent_run_id            UUID NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
        workspace_id            UUID NOT NULL,
        brand_id                UUID NULL,
        provider                VARCHAR(64) NOT NULL,
        model                   VARCHAR(96) NOT NULL,
        call_type               VARCHAR(16) NOT NULL,
        prompt_hash             VARCHAR(64) NOT NULL,
        prompt_full             TEXT NULL,
        raw_output              TEXT NULL,
        tools_called            JSONB NOT NULL DEFAULT '[]'::jsonb,
        prompt_tokens           INTEGER NOT NULL DEFAULT 0,
        completion_tokens       INTEGER NOT NULL DEFAULT 0,
        input_cost_usd          NUMERIC(14, 6) NOT NULL DEFAULT 0,
        output_cost_usd         NUMERIC(14, 6) NOT NULL DEFAULT 0,
        cost_usd                NUMERIC(14, 6) NOT NULL DEFAULT 0,
        cost_rub                NUMERIC(14, 4) NOT NULL DEFAULT 0,
        latency_ms              INTEGER NOT NULL DEFAULT 0,
        circuit_breaker_state   VARCHAR(16) NOT NULL DEFAULT 'closed',
        retries                 INTEGER NOT NULL DEFAULT 0,
        success                 BOOLEAN NOT NULL,
        error_code              VARCHAR(64) NULL,
        response_id             VARCHAR(128) NULL,
        opt_in_training         BOOLEAN NOT NULL DEFAULT false,
        created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT ck_llm_calls_call_type
            CHECK (call_type IN ('chat', 'embed', 'image')),
        CONSTRAINT ck_llm_calls_breaker_state
            CHECK (circuit_breaker_state IN ('closed', 'half_open', 'open'))
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_llm_calls_agent_run
        ON llm_calls (agent_run_id, created_at);
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_llm_calls_workspace_created
        ON llm_calls (workspace_id, created_at DESC);
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_llm_calls_provider_model_created
        ON llm_calls (provider, model, created_at DESC);
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_llm_calls_success_created
        ON llm_calls (success, created_at DESC);
    """,
)


_PG_LLM_CALLS_DAILY_PARENT_DDL = """
CREATE TABLE IF NOT EXISTS llm_calls_daily (
    id                  UUID NOT NULL,
    workspace_id        UUID NOT NULL,
    brand_id            UUID NULL,
    date                DATE NOT NULL,
    provider            VARCHAR(64) NOT NULL,
    model               VARCHAR(96) NOT NULL,
    prompt_tokens       BIGINT NOT NULL DEFAULT 0,
    completion_tokens   BIGINT NOT NULL DEFAULT 0,
    cost_usd            NUMERIC(16, 6) NOT NULL DEFAULT 0,
    cost_rub            NUMERIC(16, 4) NOT NULL DEFAULT 0,
    call_count          BIGINT NOT NULL DEFAULT 0,
    errors_count        BIGINT NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pk_llm_calls_daily
        PRIMARY KEY (workspace_id, brand_id, date, provider, model, id),
    CONSTRAINT uq_llm_calls_daily_natural
        UNIQUE (workspace_id, brand_id, date, provider, model)
) PARTITION BY RANGE (date);
"""


_PG_LLM_CALLS_DAILY_TEMPLATE_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS llm_calls_daily_template
        (LIKE llm_calls_daily INCLUDING ALL);
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_llm_calls_daily_template_ws_date
        ON llm_calls_daily_template (workspace_id, date DESC);
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_llm_calls_daily_template_brand_date
        ON llm_calls_daily_template (brand_id, date DESC)
        WHERE brand_id IS NOT NULL;
    """,
)


_PG_LLM_CALLS_DAILY_BOOTSTRAP_DDL = """
DO $bootstrap_llm_calls_daily$
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
    part_name   := 'llm_calls_daily_p' || to_char(month_start, 'YYYY_MM');

    EXECUTE format(
        'CREATE TABLE IF NOT EXISTS %I PARTITION OF llm_calls_daily '
        'FOR VALUES FROM (%L) TO (%L);',
        part_name, month_start, month_end
    );
END
$bootstrap_llm_calls_daily$;
"""


_PG_LLM_CALLS_DAILY_PARTMAN_DDL = """
DO $partman_llm_calls_daily$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_partman') THEN
        PERFORM partman.create_parent(
            p_parent_table   => 'public.llm_calls_daily',
            p_control        => 'date',
            p_type           => 'range',
            p_interval       => '1 month',
            p_template_table => 'public.llm_calls_daily_template',
            p_premake        => 2
        );
    END IF;
END
$partman_llm_calls_daily$;
"""


# ---------------------------------------------------------------------------
# RLS policies
# ---------------------------------------------------------------------------

_RLS_TABLES: tuple[tuple[str, str], ...] = (
    (
        "agent_runs",
        (
            "(workspace_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid "
            "OR current_setting('app.platform_role', true) IN ('admin', 'support'))"
        ),
    ),
    (
        "llm_calls",
        (
            "(workspace_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid "
            "OR current_setting('app.platform_role', true) IN ('admin', 'support'))"
        ),
    ),
    (
        "llm_calls_daily",
        (
            "(workspace_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid "
            "OR current_setting('app.platform_role', true) IN ('admin', 'support'))"
        ),
    ),
)


# ---------------------------------------------------------------------------
# Retention pg functions + pg_cron jobs (active=false)
# ---------------------------------------------------------------------------

# ``retention_chain_of_thought_run()`` zeros chain_of_thought / prompt_full
# / raw_output on rows older than 30 days that didn't opt in. Returns a
# row of counters so smoke tests can assert the function actually
# touched something.
_PG_RETENTION_COT_FN_DDL = """
CREATE OR REPLACE FUNCTION retention_chain_of_thought_run(
    horizon_days INTEGER DEFAULT 30
) RETURNS TABLE(zeroed_runs BIGINT, zeroed_calls BIGINT)
LANGUAGE plpgsql
AS $$
DECLARE
    cutoff TIMESTAMPTZ := now() - make_interval(days => horizon_days);
    n_runs BIGINT := 0;
    n_calls BIGINT := 0;
BEGIN
    WITH updated_runs AS (
        UPDATE agent_runs
        SET chain_of_thought = NULL,
            retrieved_context = NULL
        WHERE opt_in_training = false
          AND started_at < cutoff
          AND (chain_of_thought IS NOT NULL OR retrieved_context IS NOT NULL)
        RETURNING 1
    )
    SELECT count(*) INTO n_runs FROM updated_runs;

    WITH updated_calls AS (
        UPDATE llm_calls
        SET prompt_full = NULL,
            raw_output = NULL
        WHERE opt_in_training = false
          AND created_at < cutoff
          AND (prompt_full IS NOT NULL OR raw_output IS NOT NULL)
        RETURNING 1
    )
    SELECT count(*) INTO n_calls FROM updated_calls;

    RETURN QUERY SELECT n_runs, n_calls;
END
$$;
"""


# ``retention_llm_calls_aggregate_run()`` rolls raw llm_calls older than
# 90 days into the daily aggregate + deletes them. Aggregate inserts
# upsert via ``ON CONFLICT`` on the natural key.
_PG_RETENTION_AGG_FN_DDL = """
CREATE OR REPLACE FUNCTION retention_llm_calls_aggregate_run(
    horizon_days INTEGER DEFAULT 90
) RETURNS TABLE(aggregated_rows BIGINT, deleted_rows BIGINT)
LANGUAGE plpgsql
AS $$
DECLARE
    cutoff TIMESTAMPTZ := now() - make_interval(days => horizon_days);
    n_agg BIGINT := 0;
    n_deleted BIGINT := 0;
BEGIN
    WITH inserted AS (
        INSERT INTO llm_calls_daily (
            id, workspace_id, brand_id, date, provider, model,
            prompt_tokens, completion_tokens, cost_usd, cost_rub,
            call_count, errors_count
        )
        SELECT
            gen_random_uuid(),
            workspace_id,
            brand_id,
            (created_at AT TIME ZONE 'UTC')::date,
            provider,
            model,
            SUM(prompt_tokens),
            SUM(completion_tokens),
            SUM(cost_usd),
            SUM(cost_rub),
            COUNT(*),
            COUNT(*) FILTER (WHERE success IS FALSE)
        FROM llm_calls
        WHERE created_at < cutoff
        GROUP BY workspace_id, brand_id,
                 (created_at AT TIME ZONE 'UTC')::date, provider, model
        ON CONFLICT (workspace_id, brand_id, date, provider, model)
        DO UPDATE SET
            prompt_tokens     = llm_calls_daily.prompt_tokens     + EXCLUDED.prompt_tokens,
            completion_tokens = llm_calls_daily.completion_tokens + EXCLUDED.completion_tokens,
            cost_usd          = llm_calls_daily.cost_usd          + EXCLUDED.cost_usd,
            cost_rub          = llm_calls_daily.cost_rub          + EXCLUDED.cost_rub,
            call_count        = llm_calls_daily.call_count        + EXCLUDED.call_count,
            errors_count      = llm_calls_daily.errors_count      + EXCLUDED.errors_count
        RETURNING 1
    )
    SELECT count(*) INTO n_agg FROM inserted;

    WITH deleted AS (
        DELETE FROM llm_calls
        WHERE created_at < cutoff
        RETURNING 1
    )
    SELECT count(*) INTO n_deleted FROM deleted;

    RETURN QUERY SELECT n_agg, n_deleted;
END
$$;
"""


_PG_CRON_RETENTION_COT_DDL = """
DO $cron_retention_cot$
DECLARE
    job_id BIGINT;
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
        SELECT cron.schedule(
            'retention_chain_of_thought',
            '0 3 * * *',
            $job$SELECT retention_chain_of_thought_run();$job$
        ) INTO job_id;
        PERFORM cron.alter_job(job_id, active := false);
    END IF;
END
$cron_retention_cot$;
"""


_PG_CRON_RETENTION_AGG_DDL = """
DO $cron_retention_agg$
DECLARE
    job_id BIGINT;
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
        SELECT cron.schedule(
            'retention_llm_calls_aggregate',
            '30 3 * * *',
            $job$SELECT retention_llm_calls_aggregate_run();$job$
        ) INTO job_id;
        PERFORM cron.alter_job(job_id, active := false);
    END IF;
END
$cron_retention_agg$;
"""


# ---------------------------------------------------------------------------
# upgrade / downgrade
# ---------------------------------------------------------------------------


def upgrade() -> None:
    if _is_postgres():
        # 1) Per-user opt-in column.
        op.execute(_PG_ADD_OPT_IN_TRAINING_DDL)

        # 2) FX rate snapshot table.
        for stmt in _PG_FX_RATES_STATEMENTS:
            op.execute(stmt)

        # 3) agent_runs + llm_calls tables.
        for stmt in _PG_AGENT_RUNS_STATEMENTS:
            op.execute(stmt)
        for stmt in _PG_LLM_CALLS_STATEMENTS:
            op.execute(stmt)

        # 4) llm_calls_daily — partitioned monthly via pg_partman.
        op.execute(_PG_LLM_CALLS_DAILY_PARENT_DDL)
        for stmt in _PG_LLM_CALLS_DAILY_TEMPLATE_STATEMENTS:
            op.execute(stmt)
        op.execute(_PG_LLM_CALLS_DAILY_BOOTSTRAP_DDL)
        op.execute(_PG_LLM_CALLS_DAILY_PARTMAN_DDL)

        # 5) Retention pg functions + cron jobs (active=false).
        op.execute(_PG_RETENTION_COT_FN_DDL)
        op.execute(_PG_RETENTION_AGG_FN_DDL)
        op.execute(_PG_CRON_RETENTION_COT_DDL)
        op.execute(_PG_CRON_RETENTION_AGG_DDL)

        # 6) RLS policies on the three audit tables.
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

        # 7) Grant least-privilege to app_user.
        op.execute(
            """
            GRANT SELECT, INSERT, UPDATE, DELETE ON
                agent_runs, llm_calls, llm_calls_daily, fx_rates TO app_user;
            """,
        )
        return

    # SQLite (tests): flat tables, no RLS / cron / partman.
    op.add_column(
        "users",
        sa.Column(
            "opt_in_training",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    op.create_table(
        "fx_rates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("base_currency", sa.String(length=8), nullable=False),
        sa.Column("quote_currency", sa.String(length=8), nullable=False),
        sa.Column("rate", sa.Numeric(18, 8), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "source",
            sa.String(length=32),
            nullable=False,
            server_default="manual",
        ),
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
            "base_currency",
            "quote_currency",
            "observed_at",
            name="uq_fx_rates_natural",
        ),
    )
    op.create_index(
        "ix_fx_rates_observed_at",
        "fx_rates",
        ["observed_at"],
    )

    op.create_table(
        "agent_runs",
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
            sa.ForeignKey("brands.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("agent", sa.String(length=64), nullable=False),
        sa.Column(
            "agent_version",
            sa.String(length=32),
            nullable=False,
            server_default="v0",
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latency_ms", sa.BigInteger(), nullable=True),
        sa.Column(
            "prompt_tokens",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "completion_tokens",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "cost_usd",
            sa.Numeric(14, 6),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "cost_rub",
            sa.Numeric(14, 4),
            nullable=False,
            server_default="0",
        ),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "chain_of_thought",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=True,
        ),
        sa.Column(
            "retrieved_context",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=True,
        ),
        sa.Column(
            "skills_used",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column(
            "parent_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        sa.Column(
            "originator_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "opt_in_training",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "status IN ('started', 'succeeded', 'failed', 'cancelled')",
            name="ck_agent_runs_status",
        ),
    )
    op.create_index(
        "ix_agent_runs_workspace_started",
        "agent_runs",
        ["workspace_id", "started_at"],
    )
    op.create_index(
        "ix_agent_runs_brand_started",
        "agent_runs",
        ["brand_id", "started_at"],
    )
    op.create_index(
        "ix_agent_runs_agent",
        "agent_runs",
        ["agent", "started_at"],
    )
    op.create_index(
        "ix_agent_runs_parent",
        "agent_runs",
        ["parent_run_id"],
    )

    op.create_table(
        "llm_calls",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "agent_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "brand_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=96), nullable=False),
        sa.Column("call_type", sa.String(length=16), nullable=False),
        sa.Column("prompt_hash", sa.String(length=64), nullable=False),
        sa.Column("prompt_full", sa.Text(), nullable=True),
        sa.Column("raw_output", sa.Text(), nullable=True),
        sa.Column(
            "tools_called",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column(
            "prompt_tokens",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "completion_tokens",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "input_cost_usd",
            sa.Numeric(14, 6),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "output_cost_usd",
            sa.Numeric(14, 6),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "cost_usd",
            sa.Numeric(14, 6),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "cost_rub",
            sa.Numeric(14, 4),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "latency_ms",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "circuit_breaker_state",
            sa.String(length=16),
            nullable=False,
            server_default="closed",
        ),
        sa.Column(
            "retries",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("response_id", sa.String(length=128), nullable=True),
        sa.Column(
            "opt_in_training",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "call_type IN ('chat', 'embed', 'image')",
            name="ck_llm_calls_call_type",
        ),
        sa.CheckConstraint(
            "circuit_breaker_state IN ('closed', 'half_open', 'open')",
            name="ck_llm_calls_breaker_state",
        ),
    )
    op.create_index(
        "ix_llm_calls_agent_run",
        "llm_calls",
        ["agent_run_id", "created_at"],
    )
    op.create_index(
        "ix_llm_calls_workspace_created",
        "llm_calls",
        ["workspace_id", "created_at"],
    )
    op.create_index(
        "ix_llm_calls_provider_model_created",
        "llm_calls",
        ["provider", "model", "created_at"],
    )

    op.create_table(
        "llm_calls_daily",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "brand_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=96), nullable=False),
        sa.Column(
            "prompt_tokens",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "completion_tokens",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "cost_usd",
            sa.Numeric(16, 6),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "cost_rub",
            sa.Numeric(16, 4),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "call_count",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "errors_count",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint(
            "workspace_id",
            "brand_id",
            "date",
            "provider",
            "model",
            "id",
            name="pk_llm_calls_daily",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "brand_id",
            "date",
            "provider",
            "model",
            name="uq_llm_calls_daily_natural",
        ),
    )
    op.create_index(
        "ix_llm_calls_daily_ws_date",
        "llm_calls_daily",
        ["workspace_id", "date"],
    )


def downgrade() -> None:
    if _is_postgres():
        op.execute(
            """
            DO $cron_down_audit_log$
            DECLARE
                rec RECORD;
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
                    FOR rec IN
                        SELECT jobid, jobname FROM cron.job
                        WHERE jobname IN (
                            'retention_chain_of_thought',
                            'retention_llm_calls_aggregate'
                        )
                    LOOP
                        PERFORM cron.unschedule(rec.jobid);
                    END LOOP;
                END IF;
            END
            $cron_down_audit_log$;
            """,
        )

        op.execute("DROP FUNCTION IF EXISTS retention_chain_of_thought_run(integer);")
        op.execute("DROP FUNCTION IF EXISTS retention_llm_calls_aggregate_run(integer);")

        for table, _ in _RLS_TABLES:
            op.execute(f"DROP POLICY IF EXISTS {table}_isolation ON {table};")
            op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")

        op.execute("DROP TABLE IF EXISTS llm_calls_daily CASCADE;")
        op.execute("DROP TABLE IF EXISTS llm_calls_daily_template CASCADE;")
        op.execute("DROP TABLE IF EXISTS llm_calls CASCADE;")
        op.execute("DROP TABLE IF EXISTS agent_runs CASCADE;")
        op.execute("DROP TABLE IF EXISTS fx_rates CASCADE;")
        op.execute("ALTER TABLE users DROP COLUMN IF EXISTS opt_in_training;")
        return

    op.drop_index("ix_llm_calls_daily_ws_date", table_name="llm_calls_daily")
    op.drop_table("llm_calls_daily")
    op.drop_index("ix_llm_calls_provider_model_created", table_name="llm_calls")
    op.drop_index("ix_llm_calls_workspace_created", table_name="llm_calls")
    op.drop_index("ix_llm_calls_agent_run", table_name="llm_calls")
    op.drop_table("llm_calls")
    op.drop_index("ix_agent_runs_parent", table_name="agent_runs")
    op.drop_index("ix_agent_runs_agent", table_name="agent_runs")
    op.drop_index("ix_agent_runs_brand_started", table_name="agent_runs")
    op.drop_index("ix_agent_runs_workspace_started", table_name="agent_runs")
    op.drop_table("agent_runs")
    op.drop_index("ix_fx_rates_observed_at", table_name="fx_rates")
    op.drop_table("fx_rates")
    op.drop_column("users", "opt_in_training")
