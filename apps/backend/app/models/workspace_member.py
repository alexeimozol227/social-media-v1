"""WorkspaceMember ORM model.

docs/04-architecture.md §18.6: workspace-level RBAC. R3 will permit
additional rows for invited collaborators with ``role`` in
``{editor, viewer, reviewer, analyst, admin}``. R2 ships with
exactly one row per workspace (``role='owner'`` mirroring
``workspaces.owner_id``).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class WorkspaceMemberRole:
    """``workspace_members.role`` constants.

    Vocabulary stored as a string + CHECK rather than a Postgres
    enum so that adding a new role is a CHECK swap, not an enum-
    type ALTER.
    """

    OWNER = "owner"
    ADMIN = "admin"
    EDITOR = "editor"
    REVIEWER = "reviewer"
    VIEWER = "viewer"
    ANALYST = "analyst"


_MEMBER_ROLES = (
    WorkspaceMemberRole.OWNER,
    WorkspaceMemberRole.ADMIN,
    WorkspaceMemberRole.EDITOR,
    WorkspaceMemberRole.REVIEWER,
    WorkspaceMemberRole.VIEWER,
    WorkspaceMemberRole.ANALYST,
)


class WorkspaceMember(Base):
    __tablename__ = "workspace_members"
    __table_args__ = (
        CheckConstraint(
            "role IN ('owner', 'admin', 'editor', 'reviewer', 'viewer', 'analyst')",
            name="ck_workspace_members_role",
        ),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)

    # Subset of brands inside the workspace this member can act on.
    # NULL / empty = full access (typical for ``owner``); a non-empty
    # array restricts the member to those brands. docs/04-architecture
    # §18.6 covers the eventual UI for invite / role-scope edits.
    brand_ids: Mapped[list[uuid.UUID] | None] = mapped_column(
        ARRAY(UUID(as_uuid=True)).with_variant(JSON(), "sqlite"),
        nullable=True,
    )

    invited_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    joined_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


__all__ = ["WorkspaceMember", "WorkspaceMemberRole"]
