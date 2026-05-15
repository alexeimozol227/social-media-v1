"""add brands.disabled_global_skills (D70 Level 1 customization)

Revision ID: 0005_brands_skills
Revises: 0004_audit_events
Create Date: 2026-05-15

PR #6 / phase0-phase1-sprint1-plan.md — Skill infrastructure
(D68 / D69 / D70 in docs/04 §20).

Adds the per-brand opt-out array used by ``SkillCompiler`` to skip
non-``safety`` / non-``system`` skills at compile time:

    ALTER TABLE brands
    ADD COLUMN disabled_global_skills TEXT[] DEFAULT '{}';

* On **Postgres** the column is ``TEXT[]`` (per docs/04 §20.6 SQL).
* On **SQLite** (the test DB) we fall back to ``JSON`` with a ``'[]'``
  default — SQLite has no first-class array type but the JSON variant
  round-trips ``list[str]`` cleanly through SQLAlchemy.

Safety-tagged skills are filtered out at compile-time regardless of
whether the brand listed them in this column — see
``app.skills._registry.SkillCompiler.compile``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0005_brands_skills"
down_revision: str | None = "0004_audit_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _array_type() -> sa.types.TypeEngine[object]:
    """``TEXT[]`` on Postgres, ``JSON`` (with a list payload) on SQLite."""

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        return postgresql.ARRAY(sa.Text())
    return sa.JSON()


def _server_default() -> str:
    """Dialect-aware empty-array default."""

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        return "{}"
    return "[]"


def upgrade() -> None:
    op.add_column(
        "brands",
        sa.Column(
            "disabled_global_skills",
            _array_type(),
            nullable=False,
            server_default=_server_default(),
        ),
    )


def downgrade() -> None:
    op.drop_column("brands", "disabled_global_skills")
