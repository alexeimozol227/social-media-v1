"""telegram_userbot_sessions (encrypted MTProto session pool)

Revision ID: 0012_telegram_userbot_sessions
Revises: 0011_channel_post_embeddings
Create Date: 2026-05-23

PR #18 / docs/plans/phase1-sprint2-plan.md — user-bot pool
infrastructure (docs/05-tech-stack.md §5.2, D40).

Adds the table that backs the platform-level rotation pool of MTProto
user-bot accounts. The table is intentionally NOT tenant-scoped: one
user-bot reads public channels for every workspace, and the Global
Channel Registry deduplicates the channel rows across tenants.

All credential fields are Fernet-encrypted at rest. The plaintext is
only materialised in memory by
:func:`app.services.userbot_sessions.decrypt_session` right before a
:class:`Pyrogram.Client` is instantiated.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0012_telegram_userbot_sessions"
down_revision: str | None = "0011_channel_post_embeddings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "telegram_userbot_sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("phone_number", sa.String(length=32), nullable=False),
        sa.Column("account_label", sa.String(length=64), nullable=False),
        sa.Column("api_id_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("api_hash_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("session_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_healthcheck_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_healthcheck_ok", sa.Boolean(), nullable=True),
        sa.Column("flood_wait_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "usage_count_24h",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("notes", sa.Text(), nullable=True),
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
            "phone_number",
            name="uq_telegram_userbot_sessions_phone",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'disabled', 'flood_wait', 'banned')",
            name="ck_telegram_userbot_sessions_status",
        ),
    )

    op.create_index(
        "ix_telegram_userbot_sessions_rotation",
        "telegram_userbot_sessions",
        ["status", "last_used_at"],
    )

    if _is_postgres():
        # Partial index — only active rows are candidates for the
        # rotation query, so a partial keeps the index small even
        # when the table grows with banned / disabled history.
        op.execute(
            "CREATE INDEX ix_telegram_userbot_sessions_active "
            "ON telegram_userbot_sessions (status) "
            "WHERE status = 'active';"
        )
        # Least-privilege grant for the app role created in 0008.
        op.execute(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON "
            "telegram_userbot_sessions TO app_user;"
        )
    else:
        # SQLite: regular index — partial WHERE clauses are supported
        # but inconsistent across versions; the rotation index above
        # is enough for tests.
        op.create_index(
            "ix_telegram_userbot_sessions_active",
            "telegram_userbot_sessions",
            ["status"],
        )


def downgrade() -> None:
    if _is_postgres():
        op.execute("DROP INDEX IF EXISTS ix_telegram_userbot_sessions_active;")
    else:
        op.drop_index(
            "ix_telegram_userbot_sessions_active",
            table_name="telegram_userbot_sessions",
        )
    op.drop_index(
        "ix_telegram_userbot_sessions_rotation",
        table_name="telegram_userbot_sessions",
    )
    op.drop_table("telegram_userbot_sessions")
