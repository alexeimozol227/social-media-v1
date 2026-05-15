"""Workspace ORM model.

docs/04-architecture.md §18.5: a workspace owns every resource
(brands, channels, posts, agent runs, billing entitlements). MVP is
single-workspace-per-user (D-tenancy in `04 §18`), but the model
already carries the ``type`` discriminator so that switching the
account into ``agency`` / ``network`` mode in a future release is a
data flip, not a migration.
"""

from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin


class WorkspaceType:
    """``workspaces.type`` constants.

    * ``solo`` — single creator, default for self-signup. Pricing
      tier: Solo.
    * ``agency`` — agency / studio managing multiple clients. Pricing
      tier: Pro / Agency (post-MVP).
    * ``network`` — networks publishing many channels of their own.
      Pricing tier: Network.
    """

    SOLO = "solo"
    AGENCY = "agency"
    NETWORK = "network"


_WORKSPACE_TYPES = (WorkspaceType.SOLO, WorkspaceType.AGENCY, WorkspaceType.NETWORK)


class Workspace(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "workspaces"
    __table_args__ = (
        UniqueConstraint("owner_id", "slug", name="uq_workspaces_owner_slug"),
        CheckConstraint(
            "type IN ('solo', 'agency', 'network')",
            name="ck_workspaces_type",
        ),
    )

    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    type: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=WorkspaceType.SOLO,
        server_default=WorkspaceType.SOLO,
    )
    # docs/04-architecture.md §22 — multi-currency at the workspace
    # level so an agency in BYN can manage a network in RUB.
    preferred_currency: Mapped[str] = mapped_column(
        String(8), nullable=False, default="RUB", server_default="RUB"
    )

    def __repr__(self) -> str:
        return f"<Workspace {self.slug} owner={self.owner_id}>"


__all__ = ["Workspace", "WorkspaceType"]
