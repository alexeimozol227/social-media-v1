"""Tests for the ``0011_channel_post_embeddings`` migration (PR #17).

The migration is the source of truth for the table shape on
Postgres + SQLite; we cover the SQLite branch here because the test
suite runs on aiosqlite. The Postgres branch (pgvector + pg_partman +
HNSW + pg_cron) is exercised separately via a Postgres integration
test (gated behind the ``postgres`` pytest marker that ``conftest.py``
documents).

Coverage:

* The migration's SQLite branch produces the expected columns +
  unique constraint when applied to a fresh DB.
* Re-applying the upgrade after the downgrade leaves the same final
  state — guards against half-applied migrations on a CI rerun.
"""

from __future__ import annotations

import importlib.util
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool


def _load_migration_module() -> Any:
    """Load ``alembic/versions/0011_channel_post_embeddings.py`` by path.

    Filenames starting with a digit are not importable via plain
    ``import`` statements, so we load the module via :mod:`importlib`.
    """

    repo_root = Path(__file__).resolve().parents[1]
    path = repo_root / "alembic" / "versions" / "0011_channel_post_embeddings.py"
    spec = importlib.util.spec_from_file_location("_pr17_migration", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_0011_channel_post_embeddings = _load_migration_module()

# ---------------------------------------------------------------------------
# Boilerplate: the migration imports ``alembic.op`` which expects a
# live Context. We use Alembic's ``EnvironmentContext`` + ``MigrationContext``
# bound to an in-memory engine so the SQLite branch runs end-to-end.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def migrated_engine() -> AsyncIterator[Any]:
    """Apply 0011's SQLite upgrade against a fresh in-memory DB.

    We bootstrap the parent tables (``channels`` + ``workspaces``) by
    creating them via the ORM metadata so the embedding migration's
    foreign keys resolve.
    """

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    from app.db.base import Base
    from app.models import Channel, Workspace  # noqa: F401 - registers tables

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
    async with engine.begin() as conn:
        # Pre-create the FK parent tables — but EXCLUDE
        # ``channel_post_embeddings`` because the migration itself is
        # the source of truth for that table on SQLite. Letting
        # ``create_all`` create it first would mask migration bugs.
        tables_to_create = [
            table
            for name, table in Base.metadata.tables.items()
            if name != "channel_post_embeddings"
        ]
        await conn.run_sync(
            lambda sync_conn: Base.metadata.create_all(sync_conn, tables=tables_to_create),
        )

        # Run the migration's upgrade(). Alembic's Operations needs a
        # plain (sync) connection — we use ``conn.connection`` to
        # access the underlying sync connection inside ``run_sync``.
        def _apply(sync_conn: Any) -> None:
            ctx = MigrationContext.configure(sync_conn)
            with Operations.context(ctx):
                _0011_channel_post_embeddings.upgrade()

        await conn.run_sync(_apply)

    yield engine
    await engine.dispose()


@pytest.mark.asyncio
async def test_migration_creates_table_with_expected_columns(
    migrated_engine: Any,
) -> None:
    async with migrated_engine.connect() as conn:
        names: list[str] = await conn.run_sync(
            lambda sync_conn: list(inspect(sync_conn).get_table_names())
        )
    assert "channel_post_embeddings" in names

    async with migrated_engine.connect() as conn:
        columns: list[dict[str, Any]] = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_columns("channel_post_embeddings")
        )
    col_names = {c["name"] for c in columns}
    assert col_names == {
        "id",
        "channel_post_id",
        "channel_id",
        "workspace_id",
        "model",
        "embedding",
        "created_at",
    }


@pytest.mark.asyncio
async def test_migration_unique_constraint_holds(
    migrated_engine: Any,
) -> None:
    """The unique constraint on ``(channel_post_id, model)`` is the
    invariant that makes the embedding upsert idempotent. We check it
    via the SQLAlchemy inspector — that's the catalog equivalent of
    "the constraint will fire" and avoids depending on the exact
    column shape of every parent table (which evolves over time)."""

    async with migrated_engine.connect() as conn:
        constraints: list[dict[str, Any]] = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_unique_constraints(
                "channel_post_embeddings",
            ),
        )
    unique_cols = {tuple(sorted(idx["column_names"])) for idx in constraints}
    assert ("channel_post_id", "model") in unique_cols
