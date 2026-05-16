"""Competitor channel API request / response schemas (PR #18).

docs/plans/phase1-sprint2-plan.md PR #18 (Inspiration Board L1
preview): the API for connecting public competitor channels to a
brand. Mirrors the shape of :mod:`app.schemas.channels` but the
projection is named differently so the SPA can render a separate
"Competitors" tab without sniffing role strings on every payload.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Identifier = Annotated[
    str | int,
    Field(
        description=(
            "Competitor channel identifier. Either an ``@username`` (without or with"
            " the leading ``@``) or a Telegram numeric ``chat.id``. Private channels"
            " (no ``@username``) are rejected with ``COMPETITOR_NOT_PUBLIC``."
        ),
    ),
]


class ConnectCompetitorRequest(BaseModel):
    """Body of ``POST /v1/brands/{brand_id}/competitors``.

    Same single-value enum as :class:`ConnectChannelRequest` —
    Telegram on MVP, but the field is kept open so a second platform
    can land later without breaking the API.
    """

    model_config = ConfigDict(extra="forbid")

    platform: Literal["telegram"] = Field(
        default="telegram",
        description="Social platform identifier. ``telegram`` on MVP.",
    )
    identifier: Identifier

    @model_validator(mode="after")
    def _strip_identifier(self) -> ConnectCompetitorRequest:
        if isinstance(self.identifier, str):
            stripped = self.identifier.strip().removeprefix("@")
            if not stripped:
                msg = "identifier must be a non-empty @username or numeric chat id"
                raise ValueError(msg)
            object.__setattr__(self, "identifier", stripped)
        return self


class CompetitorView(BaseModel):
    """Read-side projection of a competitor channel binding.

    Flattens :class:`app.models.channel.Channel` (registry) and
    :class:`app.models.channel.WorkspaceChannel` (binding) into one
    object so the SPA doesn't need a second look-up. ``role`` is
    always ``"competitor"`` for endpoints in this module — kept on
    the payload so the SPA's typed routing stays uniform across the
    channels / competitors tabs.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(description="``workspace_channels.id`` — brand-scoped binding id.")
    channel_id: uuid.UUID = Field(description="``channels.id`` — Global Channel Registry id.")
    platform: str = Field(description="Channel platform.")
    external_id: int = Field(description="Telegram ``chat.id``.")
    username: Annotated[str | None, StringConstraints(max_length=64)] = Field(
        default=None,
        description="``@handle`` without the leading ``@``; never ``None`` for competitors.",
    )
    title: Annotated[str | None, StringConstraints(max_length=255)] = Field(
        default=None,
        description="Display name.",
    )
    role: Literal["competitor"] = Field(description="Always ``competitor`` for this endpoint.")
    connected_at: datetime
    disconnected_at: datetime | None = None


class CompetitorListResponse(BaseModel):
    """Paginated competitor list returned by
    ``GET /v1/brands/{id}/competitors``."""

    items: list[CompetitorView]
    total: int = Field(description="Total competitor bindings (across pages).")


__all__ = [
    "CompetitorListResponse",
    "CompetitorView",
    "ConnectCompetitorRequest",
]
