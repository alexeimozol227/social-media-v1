"""add TOTP 2FA columns to users

Revision ID: 0003_user_totp
Revises: 0002_email_verifications_password_resets
Create Date: 2026-05-15

PR #4. Adds the four columns the TOTP flow needs:

* ``totp_secret_enc`` — Fernet ciphertext (``v1:...``) of the RFC
  6238 shared secret. Storing the encrypted form rather than the
  raw secret keeps the threat model aligned with the rest of the
  auth work: a database snapshot leak alone is not enough to forge
  codes.
* ``totp_enrolled_at`` — wall-clock timestamp the user finished
  enrollment. NULL means "never enrolled" (the read paths skip
  ``totp_secret_enc`` if this is NULL).
* ``totp_recovery_hashes`` — JSON array of SHA-256 hex digests of
  the one-shot recovery codes. JSONB on Postgres / JSON on SQLite
  (the test DB). Each entry is consumed by removing it from the
  array.
* ``totp_last_step_up_at`` — wall-clock of the most recent
  successful ``/v1/auth/login/mfa`` / ``/mfa/enroll/confirm``. The
  ``mfa_token`` carries its own expiry; this column is a server-side
  audit hook ("when did we last admit them through 2FA?").

Adapted from the reference project's 0047 migration (PR-T9).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0003_user_totp"
down_revision: str | None = "0002_email_verifications_password_resets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json_type() -> sa.types.TypeEngine[object]:
    """JSONB on Postgres, JSON on SQLite (tests) / other dialects."""

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        return postgresql.JSONB(astext_type=sa.Text())
    return sa.JSON()


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("totp_secret_enc", sa.Text(), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column(
            "totp_enrolled_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "users",
        sa.Column("totp_recovery_hashes", _json_type(), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column(
            "totp_last_step_up_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "totp_last_step_up_at")
    op.drop_column("users", "totp_recovery_hashes")
    op.drop_column("users", "totp_enrolled_at")
    op.drop_column("users", "totp_secret_enc")
