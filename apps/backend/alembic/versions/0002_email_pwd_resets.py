"""email_verifications + password_resets

Revision ID: 0002_email_pwd_resets
Revises: 0001_initial
Create Date: 2026-05-15

PR #3: introduces the row stores backing email verification (sign-up
+ change) and password reset flows. See:

* ``app/models/email_verification.py``
* ``app/models/password_reset.py``
* ``app/services/email_verifications.py``
* ``app/services/password_reset.py``
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_email_pwd_resets"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "email_verifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("purpose", sa.String(length=16), nullable=False),
        sa.Column("code_hash", sa.String(length=255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_email_verifications"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_email_verifications_user_id_users",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "purpose IN ('signup','change')",
            name="ck_email_verifications_purpose",
        ),
    )
    op.create_index(
        "ix_email_verifications_user_purpose",
        "email_verifications",
        ["user_id", "purpose"],
    )

    op.create_table(
        "password_resets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip_requested", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_password_resets"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_password_resets_user_id_users",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("token_hash", name="uq_password_resets_token_hash"),
    )
    op.create_index(
        "ix_password_resets_user_id",
        "password_resets",
        ["user_id"],
    )
    op.create_index(
        "ix_password_resets_expires_at",
        "password_resets",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_password_resets_expires_at", table_name="password_resets")
    op.drop_index("ix_password_resets_user_id", table_name="password_resets")
    op.drop_table("password_resets")

    op.drop_index(
        "ix_email_verifications_user_purpose", table_name="email_verifications"
    )
    op.drop_table("email_verifications")
