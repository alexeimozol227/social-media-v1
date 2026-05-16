"""Channel API request / response schemas (PR #14).

docs/plans/phase1-sprint2-plan.md §"Бэкенд — API эндпоинты":

* :class:`ConnectChannelRequest` — body of
  ``POST /v1/brands/{brand_id}/channels``.
* :class:`ChannelView` — read-side projection used by both the
  list and detail responses.
* :class:`ChannelListResponse` — paginated list shape.
* :class:`BrandSummary` — used by ``GET /v1/users/me/brands`` so
  the brand-switcher UI can render the dropdown without leaking
  internal fields.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Identifier = Annotated[
    str | int,
    Field(
        description=(
            "Channel identifier. Either an ``@username`` (without or with the leading ``@``)"
            " or a Telegram numeric ``chat.id`` (negative for channels, fits in BIGINT)."
        ),
    ),
]


class ConnectChannelRequest(BaseModel):
    """Body of ``POST /v1/brands/{brand_id}/channels``.

    The ``platform`` field is currently a single-value enum
    (``telegram``) but kept open so PR #18 can add a second value
    without breaking the API. ``identifier`` is loose-typed because
    the Bot API accepts both ``@username`` (string) and numeric
    ``chat_id`` (int) — the wrapper handles the dispatch.
    """

    model_config = ConfigDict(extra="forbid")

    platform: Literal["telegram"] = Field(
        default="telegram",
        description="Social platform identifier. ``telegram`` on MVP.",
    )
    identifier: Identifier

    @model_validator(mode="after")
    def _strip_identifier(self) -> ConnectChannelRequest:
        # Trim leading ``@`` from username strings so the Bot API
        # call doesn't have to handle both shapes — also rejects
        # bare ``@`` or empty strings as 422.
        if isinstance(self.identifier, str):
            stripped = self.identifier.strip().removeprefix("@")
            if not stripped:
                msg = "identifier must be a non-empty @username or numeric chat id"
                raise ValueError(msg)
            object.__setattr__(self, "identifier", stripped)
        return self


class ChannelView(BaseModel):
    """Read-side projection of a connected channel.

    Combines :class:`Channel` (registry) + :class:`WorkspaceChannel`
    (binding) into one flat shape so the SPA doesn't have to do a
    second look-up to find the public ``@handle``.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(description="``workspace_channels.id`` — brand-scoped binding id.")
    channel_id: uuid.UUID = Field(description="``channels.id`` — Global Channel Registry id.")
    platform: str = Field(description="Channel platform.")
    external_id: int = Field(description="Telegram ``chat.id``.")
    username: Annotated[str | None, StringConstraints(max_length=64)] = Field(
        default=None,
        description="``@handle`` without the leading ``@``; None for private channels.",
    )
    title: Annotated[str | None, StringConstraints(max_length=255)] = Field(
        default=None,
        description="Display name.",
    )
    role: str = Field(description="``owned`` / ``competitor``.")
    bot_admin_rights: dict[str, Any] = Field(
        default_factory=dict,
        description="Snapshot of the bot's admin rights at connect / verify time.",
    )
    connected_at: datetime
    disconnected_at: datetime | None = None


class ChannelListResponse(BaseModel):
    """Paginated channel list returned by ``GET /v1/brands/{id}/channels``."""

    items: list[ChannelView]
    total: int = Field(description="Total channels matching the filter (across pages).")


class BrandSummary(BaseModel):
    """Brand-switcher payload (``GET /v1/users/me/brands``).

    Trims :class:`app.models.brand.Brand` down to the fields the SPA
    needs to render the dropdown.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    name: Annotated[str, StringConstraints(max_length=255)]
    is_default: bool
    content_language: Annotated[str, StringConstraints(max_length=16)]
    timezone: Annotated[str, StringConstraints(max_length=64)]


class BackfillChannelRequest(BaseModel):
    """Body of ``POST /v1/brands/{brand_id}/channels/{channel_id}/backfill``.

    ``limit`` is bounded by ``settings.telegram_backfill_max_limit``
    (the route raises :class:`ChannelBackfillLimitExceededError`
    when the caller asks for more). ``from_message_id`` lets an
    operator resume a previous run from a known anchor — leave it
    ``None`` for the most-recent window.
    """

    model_config = ConfigDict(extra="forbid")

    limit: int = Field(
        default=100,
        ge=1,
        description=(
            "Number of historical posts to fetch in this run. "
            "Hard-capped at ``settings.telegram_backfill_max_limit`` (default 500)."
        ),
    )
    from_message_id: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Exclusive upper bound on ``message_id``; pass to resume from "
            "an anchor older than the most-recent post."
        ),
    )


class BackfillChannelResponse(BaseModel):
    """Acknowledgment returned by the backfill trigger endpoint.

    The endpoint returns 202 immediately — the actual ingest happens
    asynchronously in the Celery task identified by ``task_id``. The
    SPA correlates :class:`app.events.schemas.ChannelBackfillCompletedEvent`
    on the per-user WS channel against this ``task_id`` to flip its
    "fetching history…" indicator off.
    """

    task_id: str = Field(
        description="Celery task id; mirrors ``ChannelBackfillStartedEvent.task_id``.",
    )
    workspace_channel_id: uuid.UUID = Field(
        description="``workspace_channels.id`` the backfill targets.",
    )
    requested_limit: int = Field(
        description="Post window the worker will fetch (post-clamping).",
    )


__all__ = [
    "BackfillChannelRequest",
    "BackfillChannelResponse",
    "BrandSummary",
    "ChannelListResponse",
    "ChannelView",
    "ConnectChannelRequest",
]
