"""Pydantic v2 event schemas (D32 / D41).

Source of truth: ``docs/04-architecture.md §8.3 Pydantic-типизированные
события`` + ``docs/06-roadmap.md §5 Спринт 1`` ("`apps/backend/events/
schemas.py` + Pydantic discriminated unions. Первое событие —
``user.registered``").

Wire shape (JSON, identical on both transports — Redis pubsub and
the per-user WebSocket): every event is a flat object with at
minimum::

    {
        "event_id":        "<uuid>",                # dedup at consumer
        "event_type":      "user.registered",       # discriminator
        "agent_source":    "platform.auth",         # who published
        "timestamp":       "2026-05-14T10:00:00Z",  # ISO 8601 UTC
        "idempotency_key": "<uuid>",                # retry safety
        "workspace_id":    null,                    # optional context
        "brand_id":        null,                    # optional context
        "user_id":         "<uuid>",                # routing key (per-user channel)
        ...                                         # event-type-specific payload
    }

Subclasses pin ``event_type`` to a :class:`~typing.Literal` so the
:func:`pydantic.Field` discriminator can route a raw dict back to
the right concrete class. That's both how consumers parse inbound
events (single :func:`parse_event` entry point) and how mypy
catches a publish-site that forgot a required field.

Channels are per-user (``events:user:{user_id}``) for everything
that fans out to a user's open tabs (WebSocket subscribers). The
``user_id`` field on the event doubles as the routing key — the
publisher reads it once, derives the channel, and the WS route on
the consumer side subscribes to the matching channel name. The
inter-agent event-type-keyed channels described in ``04 §8.2``
(e.g. ``content.post_generated``) are a separate fan-out plane;
they live in subsequent PRs alongside the actual agents.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


def _new_uuid_str() -> str:
    return str(uuid.uuid4())


class BaseEvent(BaseModel):
    """Common envelope fields shared by every concrete event.

    Subclasses must override ``event_type`` with a :class:`~typing.Literal`
    so the discriminator on :data:`Event` works. ``agent_source`` is
    a free-form dotted identifier (``platform.auth``, ``agent.content``,
    ``agent.publisher``) so consumers can attribute / filter without
    parsing the event-type string itself.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(
        default_factory=_new_uuid_str,
        description="Unique event identifier (UUIDv4). Consumer-side dedup key.",
    )
    event_type: str = Field(
        description=(
            "Dot-separated discriminator. Subclasses pin this to a Literal."
        ),
    )
    agent_source: str = Field(
        description=(
            "Publisher identifier (``platform.<module>`` or "
            "``agent.<name>``). Used for attribution + filtering."
        ),
    )
    workspace_id: str | None = Field(
        default=None,
        description="Workspace context (UUID string), or None for system events.",
    )
    brand_id: str | None = Field(
        default=None,
        description="Brand context (UUID string), or None for non-brand events.",
    )
    user_id: str | None = Field(
        default=None,
        description=(
            "Target user (UUID string). When set, doubles as the per-user "
            "channel routing key on the WebSocket fan-out plane."
        ),
    )
    timestamp: datetime = Field(
        default_factory=_utcnow,
        description="UTC ISO-8601 timestamp the publisher created the event.",
    )
    idempotency_key: str = Field(
        default_factory=_new_uuid_str,
        description=(
            "Stable key for at-least-once retries — consumers MUST dedup "
            "on this when handling critical pipelines (publication, billing). "
            "Defaults to a fresh UUID if the publisher doesn't supply one."
        ),
    )


class UserRegisteredEvent(BaseEvent):
    """First platform event: a brand-new account just finished sign-up.

    Published by :func:`app.api.routes.auth.register` right after the
    sign-up transaction commits. The user's tabs (already opened on
    ``/register`` → ``/dashboard``) subscribe to their per-user channel
    over WebSocket and render the welcome toast on receipt.

    Publishes are best-effort — see :mod:`app.core.event_bus`. If the
    Redis publish blips, the sign-up itself stays green and the SPA
    falls back to its own ``Welcome, <email>!`` static greeting.
    """

    event_type: Literal["user.registered"] = "user.registered"
    agent_source: Literal["platform.auth"] = "platform.auth"

    email: str = Field(description="The user's email (already lower-cased).")
    locale: str = Field(description="``users.locale`` at sign-up (e.g. ``ru-RU``).")
    default_workspace_id: str = Field(
        description="UUID of the default workspace created in the same transaction.",
    )


# Discriminated union — every new event-type subclass goes here.
Event = Annotated[
    UserRegisteredEvent,
    Field(discriminator="event_type"),
]


class EventEnvelope(RootModel[Event]):
    """Wrapper for parsing arbitrary inbound events.

    Use :func:`parse_event` for a one-line ``dict → concrete event``
    helper that doesn't leak the RootModel construction detail.
    """


def parse_event(raw: dict[str, object]) -> Event:
    """Parse a JSON-decoded dict back into the matching concrete event.

    Raises :class:`pydantic.ValidationError` for unknown ``event_type``
    or schema violations — callers turn that into a dropped frame +
    log line rather than crashing the consumer loop.
    """

    return EventEnvelope.model_validate(raw).root
