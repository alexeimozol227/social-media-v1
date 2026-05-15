"""create idempotency_keys table

Revision ID: 0006_idempotency_keys
Revises: 0005_brands_skills
Create Date: 2026-05-15

PR #8 / phase0-phase1-sprint1-plan.md — Idempotency middleware
(П13 in docs/04, docs/05 §2.3.7 + §2.5).

The authoritative cache lives in Redis with a 24 h TTL; this
Postgres table is a durable audit copy. Rows older than 7 days
will be cleaned up by a future ``pg_cron`` retention job.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0006_idempotency_keys"
down_revision: str = "0005_brands_skills"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.create_table(
        "idempotency_keys",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("key", sa.String(256), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("method", sa.String(10), nullable=False),
        sa.Column("path", sa.String(2048), nullable=False),
        sa.Column("response_status", sa.Integer, nullable=False),
        sa.Column("response_body", sa.Text, nullable=True),
        sa.Column("response_media_type", sa.String(128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_index(
        "ix_idempotency_keys_user_key",
        "idempotency_keys",
        ["user_id", "key"],
        unique=True,
    )
    op.create_index(
        "ix_idempotency_keys_created_at",
        "idempotency_keys",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_idempotency_keys_created_at", table_name="idempotency_keys")
    op.drop_index("ix_idempotency_keys_user_key", table_name="idempotency_keys")
    op.drop_table("idempotency_keys")
