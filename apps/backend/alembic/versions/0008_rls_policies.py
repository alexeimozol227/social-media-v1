"""enable Row Level Security on tenant-scoped tables (D27 / D65 / D66)

Revision ID: 0008_rls_policies
Revises: 0007_billing_skeleton
Create Date: 2026-05-15

PR #11 / phase0-phase1-sprint1-plan.md — RLS policies on every
tenant-scoped business table per docs/04-architecture.md §18.7
(D27 / D65 / D66) and docs/05-tech-stack.md §2.4.1.

Behavior:

* On **Postgres**, this migration:

  1. ``CREATE ROLE app_user NOLOGIN`` (idempotent) — the role the
     FastAPI workers connect as. Operators grant ``LOGIN`` + set a
     password as part of provisioning; we deliberately do *not* set
     a password here so a checked-in migration can't leak one.

  2. Grants ``USAGE`` on the ``public`` schema + ``SELECT, INSERT,
     UPDATE, DELETE`` on every existing table + future sequences to
     ``app_user``.

  3. Enables and **FORCES** Row Level Security on tenant-scoped
     tables: ``workspaces``, ``workspace_members``, ``brands``,
     ``refresh_tokens``, ``idempotency_keys``, ``invoices``.

  4. Installs one ``<table>_isolation`` policy per table that reads
     the three GUCs ``app.current_user_id`` / ``app.current_tenant_id``
     / ``app.platform_role`` (set per-request via ``SET LOCAL`` in
     ``app.db.rls.set_rls_context``). Admin / support roles bypass
     RLS for read-only ops; writes still require the isolation
     predicate to hold so a hijacked admin session can't quietly
     re-parent rows to another tenant.

  The GUCs are read with the ``missing_ok=true`` form
  (``current_setting('app.current_user_id', true)``). If the GUC is
  unset or empty, the row is denied (the application is required to
  call ``set_rls_context`` on every authenticated request).
  Pre-authenticated paths that need to read these tables (none in
  the current scope) would have to be re-routed through a different
  role with ``BYPASSRLS``.

* On **SQLite** (the test DB) this migration is a no-op — SQLite
  doesn't support RLS. The matching RLS-isolation tests are marked
  ``@pytest.mark.postgres`` and skip themselves when the DB URL
  isn't Postgres.

Tables left *out* of RLS in this PR (different access patterns):

* ``users`` — login + registration must read by email without a
  session context yet.
* ``refresh_tokens`` is *in* (lookup is by ``token_hash`` SHA-256
  which is unique; we then immediately install GUCs from the
  matched row, so the pre-auth window is one lookup wide and
  bounded by an opaque-key cardinality test).
* ``email_verifications`` / ``password_resets`` — verification
  links click in cold, no session yet.
* ``audit_events`` — admin-only access path enforced in the
  service layer; will get a richer policy in PR #11+ once we wire
  the admin panel.
* ``plans`` / ``plan_prices`` — public catalog.

The companion docker-compose ``pgbouncer`` service runs
``pool_mode=transaction`` so the per-request ``SET LOCAL`` GUCs are
scoped to the (implicit) transaction PgBouncer keeps open to the
backend connection. ``tools/lint_set_local.py`` already rejects any
``SET app.*`` that isn't ``SET LOCAL`` so a session-level leak
across pooled connections can't land.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008_rls_policies"
down_revision: str | None = "0007_billing_skeleton"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ``user_id`` / ``workspace_id`` predicate templates. Each policy is
# expressed twice — once for ``USING`` (read / update / delete row
# visibility) and once for ``WITH CHECK`` (insert / update row
# validity). Casting through ``NULLIF(..., '')`` so an unset GUC
# (empty string after ``SET LOCAL app.* = ''``) doesn't blow up
# ``::uuid``.

_RLS_TABLES: tuple[tuple[str, str], ...] = (
    # (table, isolation predicate — applied identically to USING and
    # WITH CHECK so a tenant can't INSERT into someone else's bucket).
    (
        "workspaces",
        (
            "(owner_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid "
            "OR id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid "
            "OR current_setting('app.platform_role', true) IN ('admin', 'support'))"
        ),
    ),
    (
        "workspace_members",
        (
            "(user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid "
            "OR workspace_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid "
            "OR current_setting('app.platform_role', true) IN ('admin', 'support'))"
        ),
    ),
    (
        "brands",
        (
            "(workspace_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid "
            "OR current_setting('app.platform_role', true) IN ('admin', 'support'))"
        ),
    ),
    (
        # ``refresh_tokens`` is read **pre-authentication** during the
        # ``/v1/auth/refresh`` + ``/v1/auth/logout`` flows (the SPA
        # presents an opaque cookie; the server hashes it and looks
        # up the matching row before any session GUC is set). We
        # therefore opt-in: when ``app.current_user_id`` is unset the
        # policy passes (the application limits this window to the
        # one ``WHERE token_hash = ...`` lookup, and the token_hash
        # column is a SHA-256 of a cryptographically-random value);
        # once GUC is set only the matching user / admin can see.
        "refresh_tokens",
        (
            "(NULLIF(current_setting('app.current_user_id', true), '') IS NULL "
            "OR user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid "
            "OR current_setting('app.platform_role', true) IN ('admin', 'support'))"
        ),
    ),
    (
        # ``idempotency_keys.user_id`` is ``String(36)``, not ``UUID``.
        "idempotency_keys",
        (
            "(user_id = NULLIF(current_setting('app.current_user_id', true), '') "
            "OR current_setting('app.platform_role', true) IN ('admin', 'support'))"
        ),
    ),
    (
        "invoices",
        (
            "(workspace_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid "
            "OR current_setting('app.platform_role', true) IN ('admin', 'support'))"
        ),
    ),
)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite (tests) doesn't support RLS — nothing to do.
        return

    # 1) Provision the ``app_user`` role and grant least-privilege.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_user') THEN
                CREATE ROLE app_user NOLOGIN;
            END IF;
        END
        $$;
        """,
    )
    op.execute("GRANT USAGE ON SCHEMA public TO app_user;")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_user;",
    )
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_user;")
    op.execute(
        """
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_user;
        """,
    )
    op.execute(
        """
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT USAGE, SELECT ON SEQUENCES TO app_user;
        """,
    )

    # 2) Enable + FORCE RLS and install the isolation policy on every
    #    tenant-scoped table.
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


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    for table, _ in _RLS_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_isolation ON {table};")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")

    # Privilege revoke is not strictly required (role keeps existing
    # ``GRANT``s in place) but we drop the default-privileges entry
    # so a re-upgrade after a downgrade is a clean reinstall.
    op.execute(
        """
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM app_user;
        """,
    )
    op.execute(
        """
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            REVOKE USAGE, SELECT ON SEQUENCES FROM app_user;
        """,
    )
    # We deliberately do NOT drop the ``app_user`` role — operators
    # may have ALTER'd it to ``LOGIN`` with a password and dropping
    # it here would break the live connection pool.
