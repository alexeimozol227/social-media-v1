"""``audit_events`` ORM model.

docs/04-architecture.md §10.1 + §11 + D57:

    audit_events  -- sensitive ops (login, password-change, MFA-toggle, ...)
      id, user_id (FK), workspace_id (FK, nullable),
      event_type, severity ENUM('info','warning','critical'),
      ip_address, user_agent, metadata JSONB,
      created_at

Append-only by convention — there is no ``update`` path in
:mod:`app.services.audit`. The table is monthly-partitioned via
pg_partman on Postgres (migration ``0004_audit_events``); on SQLite
the test DB falls back to a single flat table. The ORM doesn't care
which one it talks to: the partition key (``created_at``) is part of
the composite primary key in Postgres but the mapped class treats
``id`` as the logical identity since rows are referenced one-at-a-time.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

Severity = Literal["info", "warning", "critical"]


class AuditSeverity:
    """Constants for the ``audit_events.severity`` column.

    Stored as plain ``String(16)`` + ``CHECK`` so adding a value in
    a follow-up release is a CHECK swap rather than a Postgres
    enum-type ALTER.
    """

    INFO: Severity = "info"
    WARNING: Severity = "warning"
    CRITICAL: Severity = "critical"


_SEVERITY_VALUES: tuple[Severity, ...] = (
    AuditSeverity.INFO,
    AuditSeverity.WARNING,
    AuditSeverity.CRITICAL,
)


class AuditEvent(Base):
    """One row per sensitive operation.

    Indexes (declared on the template table in the Postgres migration
    so pg_partman copies them to each partition; declared on the flat
    table directly on SQLite):

    * ``(user_id, created_at DESC)`` — per-user audit trail.
    * ``(event_type, created_at DESC)`` — global-by-verb lens (e.g.
      "show me every ``user.login_failed`` last 24h").
    * ``(workspace_id, severity, created_at DESC) WHERE severity =
      'critical'`` — partial index for the critical-events queue.
    """

    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint(
            "severity IN ('info', 'warning', 'critical')",
            name="ck_audit_events_severity",
        ),
        Index(
            "ix_audit_events_user_id_created_at",
            "user_id",
            "created_at",
        ),
        Index(
            "ix_audit_events_event_type_created_at",
            "event_type",
            "created_at",
        ),
        Index(
            "ix_audit_events_workspace_critical",
            "workspace_id",
            "severity",
            "created_at",
        ),
    )

    # ``id`` is the logical identity. On Postgres the partition key
    # ``created_at`` is part of the composite PK declared in the
    # migration; SQLAlchemy is happy treating ``id`` as the mapper
    # identity since lookups are always by ``id`` (never by full
    # composite).
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(
        String(64).with_variant(INET(), "postgresql"),
        nullable=True,
    )
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSONB on Postgres, JSON on SQLite. Use ``meta`` as the Python
    # attribute name to avoid clashing with SQLAlchemy's
    # ``Base.metadata`` while keeping the column name from the spec.
    meta: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
        default=dict,
    )
    # ``created_at`` is also half of the composite PK on Postgres
    # (required for RANGE partitioning), but we don't mark it
    # ``primary_key=True`` here because the SQLite test schema uses a
    # plain single-column PK on ``id``. ORM identity is ``id`` on
    # both dialects; inserts/selects work regardless.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


__all__ = ["AuditEvent", "AuditSeverity", "Severity"]
