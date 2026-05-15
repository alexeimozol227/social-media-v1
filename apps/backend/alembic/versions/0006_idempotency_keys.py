"""idempotency_keys (PR #8 — П13 + docs/05 §2.3 / §2.4.5).

Revision ID: 0006_idempotency_keys
Revises: 0005_brands_skills
Create Date: 2026-05-15

Adds the cache table for the ``Idempotency-Key`` middleware. One row
per ``(actor_key, method, path, idempotency_key)`` tuple; rows
expire after the configured TTL (default 24h, per docs/05 §2.4.5).
``response_*`` columns are nullable so the middleware can insert a
row before the wrapped handler runs and update it on the way out.

On **Postgres**: ``response_headers`` is ``JSONB`` (atomic GIN-friendly
lookups, future indexing if needed), ``response_body`` is ``BYTEA``.

On **SQLite** (test DB): ``JSON`` and ``BLOB`` — same SQLAlchemy types
round-trip cleanly through both dialects.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0006_idempotency_keys"
down_revision: str | None = "0005_brands_skills"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _id_type() -> sa.types.TypeEngine[object]:
    """UUID on Postgres, ``CHAR(36)`` on SQLite."""

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        return postgresql.UUID(as_uuid=True)
    return sa.String(36)


def _headers_type() -> sa.types.TypeEngine[object]:
    """JSONB on Postgres, JSON on SQLite."""

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        return postgresql.JSONB()
    return sa.JSON()


def upgrade() -> None:
    op.create_table(
        "idempotency_keys",
        sa.Column("id", _id_type(), primary_key=True, nullable=False),
        sa.Column("actor_key", sa.String(length=128), nullable=False),
        sa.Column("method", sa.String(length=16), nullable=False),
        sa.Column("path", sa.String(length=512), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_headers", _headers_type(), nullable=True),
        sa.Column("response_body", sa.LargeBinary(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "actor_key",
            "method",
            "path",
            "idempotency_key",
            name="uq_idempotency_keys_actor_method_path_key",
        ),
    )
    op.create_index(
        "ix_idempotency_keys_expires_at",
        "idempotency_keys",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_idempotency_keys_expires_at", table_name="idempotency_keys")
    op.drop_table("idempotency_keys")
