"""Postgres-only integration tests for the PR #11 RLS policies.

docs/04-architecture.md §18.7 + D27 / D65 / D66: two unrelated tenants
must never see each other's ``workspaces`` / ``workspace_members`` /
``brands`` / ``refresh_tokens`` / ``idempotency_keys`` / ``invoices``
rows.

These tests are skipped on SQLite (the default in-memory test DB has
no Row Level Security primitives). On Postgres they:

1. Run ``alembic upgrade head`` against the live DB so the
   ``0008_rls_policies`` migration installs ``app_user`` + policies.
2. ``SET ROLE app_user`` on the test session — superusers and the
   table owner bypass RLS even with ``FORCE``, and the migration
   leaves ``app_user`` as ``NOLOGIN`` so only ``SET ROLE`` paths can
   act through it.
3. Bootstrap two unrelated users + workspaces with the GUC pinned
   to each user respectively (matching how
   :func:`app.services.workspaces.ensure_default` does it in the
   real registration handler).
4. Assert that listing rows under user A's GUC never returns user
   B's data, and that an INSERT into user B's workspace from user
   A's context is rejected by the policy.
"""

from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

pytestmark = pytest.mark.postgres


def _requires_postgres() -> str:
    """Return the DSN if the test should run, else ``""`` so it skips."""

    raw = os.environ.get("DATABASE_URL", "")
    return raw if raw.startswith("postgresql+asyncpg://") else ""


async def _run_alembic_upgrade(connection: AsyncConnection) -> None:
    """Run ``alembic upgrade head`` against ``connection``'s engine.

    We don't shell out to the ``alembic`` CLI here; instead we call
    Alembic's Python API so the upgrade is bound to the same engine
    + transaction the test uses, and so a failing migration surfaces
    as a normal pytest failure with the SQL traceback inline.
    """

    import anyio
    from alembic.config import Config

    from alembic import command

    cfg = Config()
    cfg.set_main_option("script_location", "alembic")
    cfg.set_main_option(
        "sqlalchemy.url",
        # ``alembic upgrade head`` runs sync, but ``alembic.env`` is
        # the async variant; it reads ``settings.database_url``
        # which is already pointed at this Postgres by the CI env.
        os.environ["DATABASE_URL"],
    )

    def _do_upgrade() -> None:
        command.upgrade(cfg, "head")

    # ``command.upgrade`` opens its own engine via env.py, so we
    # don't pass ``connection`` directly; it just needs the URL.
    await anyio.to_thread.run_sync(_do_upgrade)


@pytest_asyncio.fixture
async def pg_engine():
    """Bring up a Postgres engine + the latest schema for this test.

    Skips at fixture-resolution time if ``DATABASE_URL`` isn't an
    asyncpg URL. The engine is disposed at fixture teardown so each
    test starts from a clean connection pool.
    """

    dsn = _requires_postgres()
    if not dsn:
        pytest.skip("DATABASE_URL is not Postgres+asyncpg; RLS test requires Postgres")

    engine = create_async_engine(dsn, echo=False)

    # Run migrations + truncate the tables this test touches so a
    # rerun on the same DB is hermetic.
    async with engine.begin() as conn:
        await _run_alembic_upgrade(conn)

    async with engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE TABLE "
                "invoices, idempotency_keys, refresh_tokens, brands, "
                "workspace_members, workspaces, users "
                "RESTART IDENTITY CASCADE;",
            ),
        )

    yield engine
    await engine.dispose()


async def _bootstrap_user_and_workspace(engine, *, email: str) -> tuple[uuid.UUID, uuid.UUID]:
    """INSERT a user + workspace + owner-membership + default brand.

    The bootstrap runs as the connection's owner role (superuser on
    a fresh CI Postgres). RLS is enforced only against ``app_user``
    via ``SET ROLE`` in the actual isolation checks, so this fixture
    helper doesn't need to thread GUCs through itself.
    """

    user_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO users (id, email, hashed_password, status, "
                "platform_role, token_version, locale, timezone, "
                "preferred_currency) "
                "VALUES (:id, :email, '$2b$12$abcdefghijklmnopqrstuv', "
                "'active', 'user', 0, 'ru-RU', 'Europe/Minsk', 'RUB')"
            ),
            {"id": str(user_id), "email": email},
        )
        await conn.execute(
            text(
                "INSERT INTO workspaces (id, owner_id, name, slug, type, "
                "preferred_currency) "
                "VALUES (:id, :owner_id, 'Personal', 'default', 'solo', 'RUB')"
            ),
            {"id": str(workspace_id), "owner_id": str(user_id)},
        )
        await conn.execute(
            text(
                "INSERT INTO workspace_members (workspace_id, user_id, role) "
                "VALUES (:ws, :u, 'owner')"
            ),
            {"ws": str(workspace_id), "u": str(user_id)},
        )
        await conn.execute(
            text(
                "INSERT INTO brands (id, workspace_id, name, content_language, "
                "timezone, disabled_global_skills) "
                "VALUES (:id, :ws, 'My Brand', 'ru', 'Europe/Minsk', '{}')"
            ),
            {"id": str(uuid.uuid4()), "ws": str(workspace_id)},
        )

    return user_id, workspace_id


async def _set_rls_guc(
    conn: AsyncConnection,
    *,
    user_id: uuid.UUID | None,
    tenant_id: uuid.UUID | None,
    role: str = "user",
) -> None:
    """Install the per-request GUCs the FastAPI deps install in prod."""

    await conn.execute(
        text(
            f"SET LOCAL app.current_user_id = '{user_id}'"
            if user_id
            else "SET LOCAL app.current_user_id = ''"
        ),
    )
    await conn.execute(
        text(
            f"SET LOCAL app.current_tenant_id = '{tenant_id}'"
            if tenant_id
            else "SET LOCAL app.current_tenant_id = ''"
        ),
    )
    await conn.execute(text(f"SET LOCAL app.platform_role = '{role}'"))


@pytest.mark.asyncio
async def test_rls_workspaces_isolation(pg_engine) -> None:
    """User A pinned via GUC must never see user B's workspace row."""

    user_a, ws_a = await _bootstrap_user_and_workspace(pg_engine, email="a@rls-test.local")
    user_b, ws_b = await _bootstrap_user_and_workspace(pg_engine, email="b@rls-test.local")

    # User A's view.
    async with pg_engine.begin() as conn:
        await conn.execute(text("SET LOCAL ROLE app_user"))
        await _set_rls_guc(conn, user_id=user_a, tenant_id=ws_a)

        result = await conn.execute(text("SELECT id, owner_id FROM workspaces"))
        rows = result.all()
    assert len(rows) == 1, f"User A should see exactly 1 workspace; saw {rows!r}"
    assert rows[0].owner_id == user_a

    # User B's view.
    async with pg_engine.begin() as conn:
        await conn.execute(text("SET LOCAL ROLE app_user"))
        await _set_rls_guc(conn, user_id=user_b, tenant_id=ws_b)

        result = await conn.execute(text("SELECT id, owner_id FROM workspaces"))
        rows = result.all()
    assert len(rows) == 1
    assert rows[0].owner_id == user_b


@pytest.mark.asyncio
async def test_rls_brands_isolation(pg_engine) -> None:
    """A brand row is visible only to the tenant whose workspace owns it."""

    user_a, ws_a = await _bootstrap_user_and_workspace(pg_engine, email="a@rls-brands.local")
    user_b, ws_b = await _bootstrap_user_and_workspace(pg_engine, email="b@rls-brands.local")

    async with pg_engine.begin() as conn:
        await conn.execute(text("SET LOCAL ROLE app_user"))
        await _set_rls_guc(conn, user_id=user_a, tenant_id=ws_a)
        result = await conn.execute(text("SELECT workspace_id FROM brands"))
        rows = result.all()
    assert {r.workspace_id for r in rows} == {ws_a}

    async with pg_engine.begin() as conn:
        await conn.execute(text("SET LOCAL ROLE app_user"))
        await _set_rls_guc(conn, user_id=user_b, tenant_id=ws_b)
        result = await conn.execute(text("SELECT workspace_id FROM brands"))
        rows = result.all()
    assert {r.workspace_id for r in rows} == {ws_b}


@pytest.mark.asyncio
async def test_rls_cross_tenant_insert_denied(pg_engine) -> None:
    """User A cannot INSERT a brand into User B's workspace.

    The ``brands_isolation`` policy's ``WITH CHECK`` rejects any
    INSERT whose ``workspace_id`` doesn't match the GUC tenant
    (modulo the admin bypass — User A is a regular ``user``).
    """

    user_a, ws_a = await _bootstrap_user_and_workspace(pg_engine, email="a@rls-deny.local")
    _user_b, ws_b = await _bootstrap_user_and_workspace(pg_engine, email="b@rls-deny.local")

    rogue_brand_id = uuid.uuid4()
    with pytest.raises((ProgrammingError, Exception)) as excinfo:
        async with pg_engine.begin() as conn:
            await conn.execute(text("SET LOCAL ROLE app_user"))
            await _set_rls_guc(conn, user_id=user_a, tenant_id=ws_a)
            await conn.execute(
                text(
                    "INSERT INTO brands (id, workspace_id, name, content_language, "
                    "timezone, disabled_global_skills) "
                    "VALUES (:id, :ws, 'Rogue', 'ru', 'Europe/Minsk', '{}')"
                ),
                {"id": str(rogue_brand_id), "ws": str(ws_b)},
            )

    # Postgres raises ``new row violates row-level security policy`` on
    # a denied WITH CHECK; the exact class depends on the driver but
    # the message is stable enough to assert on.
    assert "row-level security" in str(excinfo.value).lower()


@pytest.mark.asyncio
async def test_rls_admin_bypass_reads_all_tenants(pg_engine) -> None:
    """``platform_role='admin'`` short-circuits the isolation predicate."""

    await _bootstrap_user_and_workspace(pg_engine, email="a@rls-admin.local")
    await _bootstrap_user_and_workspace(pg_engine, email="b@rls-admin.local")

    async with pg_engine.begin() as conn:
        await conn.execute(text("SET LOCAL ROLE app_user"))
        # GUCs pinned to a random non-tenant user; admin role on
        # alone should make every workspace visible.
        await _set_rls_guc(
            conn,
            user_id=uuid.uuid4(),
            tenant_id=None,
            role="admin",
        )
        result = await conn.execute(text("SELECT id FROM workspaces"))
        rows = result.all()

    assert len(rows) == 2
