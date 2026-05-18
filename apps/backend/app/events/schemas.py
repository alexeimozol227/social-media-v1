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
        description=("Dot-separated discriminator. Subclasses pin this to a Literal."),
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


class AuthRefreshRequiredEvent(BaseEvent):
    """Membership / role change that demands a fresh access token (D64).

    docs/04-architecture.md §18.6 + docs/05-tech-stack.md §4.5: any
    mutation of ``workspace_members`` (invite, role edit, removal)
    invalidates the Redis ``user:{id}:memberships`` cache *and*
    publishes this event on the affected user's per-user WS channel.

    The SPA reacts by issuing a one-shot ``POST /v1/auth/refresh``
    — the refresh path re-issues the access token with the updated
    ``active_workspace_id`` / ``platform_role`` claims and the
    backend serves subsequent requests against the fresh cache
    entry. The user doesn't have to sign out, the previous access
    token expires naturally at the 15-min TTL.

    ``reason`` is a free-form discriminator the UI can use to render
    a contextual toast (``role_changed`` → "Your role has been
    updated", ``invite_revoked`` → "Your access was revoked", etc.).
    """

    event_type: Literal["auth.refresh_required"] = "auth.refresh_required"
    agent_source: Literal["platform.auth"] = "platform.auth"

    reason: str = Field(
        description=(
            "Cause of the refresh request. Free-form slug; the SPA "
            "may render contextual UI on known values."
        ),
    )


class ChannelConnectedEvent(BaseEvent):
    """A brand just connected a social channel (PR #14).

    docs/plans/phase1-sprint2-plan.md §"i18n / event bus":
    published right after the connect-channel transaction commits.
    PR #14 has no subscriber yet — the WS-toast on the channels
    dashboard lands in PR #19. We still publish so the contract is
    in place from day one.
    """

    event_type: Literal["channel.connected"] = "channel.connected"
    agent_source: Literal["platform.api"] = "platform.api"

    channel_id: str = Field(description="Global Channel Registry UUID.")
    workspace_channel_id: str = Field(
        description="workspace_channels row UUID; lets the consumer"
        " look up the brand-scoped binding directly.",
    )
    platform: str = Field(description="Channel platform (``telegram`` on MVP).")
    title: str | None = Field(
        default=None,
        description="Channel display name at connect time (snapshot).",
    )
    username: str | None = Field(
        default=None,
        description="``@handle`` without the leading ``@``; None for private channels.",
    )


class ChannelDetachedEvent(BaseEvent):
    """A brand soft-detached a previously-connected channel (PR #14).

    Mirrors :class:`ChannelConnectedEvent`. The audit trail and post
    history stay intact; the row in ``workspace_channels`` just gets
    ``disconnected_at = now()``.
    """

    event_type: Literal["channel.detached"] = "channel.detached"
    agent_source: Literal["platform.api"] = "platform.api"

    channel_id: str = Field(description="Global Channel Registry UUID.")
    workspace_channel_id: str = Field(
        description="workspace_channels row UUID that was detached.",
    )
    platform: str = Field(description="Channel platform (``telegram`` on MVP).")


class ChannelBackfillStartedEvent(BaseEvent):
    """User-triggered history backfill has been accepted (PR #15).

    Emitted by the API handler right after the Celery task is enqueued.
    The dashboard renders a transient "fetching history…" state in
    response so the user sees acknowledgment within one tick rather
    than waiting for the worker's first ingest batch.
    """

    event_type: Literal["channel.backfill_started"] = "channel.backfill_started"
    agent_source: Literal["platform.api"] = "platform.api"

    channel_id: str = Field(description="Global Channel Registry UUID.")
    workspace_channel_id: str = Field(
        description="workspace_channels row UUID the backfill targets.",
    )
    task_id: str = Field(description="Celery task id; lets the SPA correlate completion.")
    requested_limit: int = Field(
        description="Per-request post limit the API forwarded to the worker.",
    )


class ChannelBackfillCompletedEvent(BaseEvent):
    """Backfill Celery task finished one ingest run (PR #15).

    Emitted at the end of the worker run regardless of outcome. The
    ``status`` field is ``"ok"`` for happy-path runs and a free-form
    failure slug otherwise (``"transport_error"`` / ``"not_connected"``
    / ``"adapter_unsupported"``). The SPA replaces the transient
    spinner with a toast on receipt.
    """

    event_type: Literal["channel.backfill_completed"] = "channel.backfill_completed"
    agent_source: Literal["agent.publisher"] = "agent.publisher"

    channel_id: str = Field(description="Global Channel Registry UUID.")
    workspace_channel_id: str = Field(
        description="workspace_channels row UUID the backfill targeted.",
    )
    task_id: str = Field(description="Celery task id (mirrors ``ChannelBackfillStartedEvent``).")
    status: str = Field(
        description=(
            "``ok`` for happy-path runs, free-form slug for failures "
            "(``transport_error`` / ``not_connected`` / "
            "``adapter_unsupported`` / ``no_history``)."
        ),
    )
    fetched_count: int = Field(
        default=0,
        description="Snapshots the adapter returned (pre-dedup).",
    )
    inserted_count: int = Field(
        default=0,
        description="New channel_posts rows written (post-dedup).",
    )
    duplicate_count: int = Field(
        default=0,
        description="Snapshots skipped because the (channel_id, tg_message_id) row already existed.",
    )


class ChannelPostReceivedEvent(BaseEvent):
    """One post was ingested into ``channel_posts`` (PR #15 + PR #16).

    Published per-post by the backfill task (``ingest_source="backfill"``)
    and by the live webhook ingestor (``ingest_source="webhook"``).
    Downstream subscribers (Brand Memory updater, embeddings job,
    analyst summarizer) react asynchronously. We keep the payload
    small — only the routing keys and minimal metadata — so consumers
    fetch the full row from the DB if they need it.
    """

    event_type: Literal["channel.post_received"] = "channel.post_received"
    agent_source: Literal["agent.publisher"] = "agent.publisher"

    channel_id: str = Field(description="Global Channel Registry UUID.")
    workspace_channel_id: str = Field(
        description="workspace_channels row UUID the post belongs to.",
    )
    channel_post_id: str = Field(description="channel_posts row UUID.")
    tg_message_id: int = Field(description="Telegram ``message_id`` (dedup key).")
    posted_at: datetime = Field(description="UTC timestamp the post was published.")
    has_media: bool = Field(
        default=False,
        description="True if the original post carried media.",
    )
    ingest_source: str = Field(
        description=(
            "Origin of the ingest event (``backfill`` / ``webhook`` / "
            "``userbot``). The Brand Memory updater treats backfilled "
            "posts as historical and live posts as fresh signal."
        ),
    )


class CompetitorConnectedEvent(BaseEvent):
    """A competitor channel was just connected to a brand (PR #18).

    Mirrors :class:`ChannelConnectedEvent` but distinct on the event
    bus so subscribers (Inspiration Board L1, Sprint 9) can filter
    without inspecting ``workspace_channels.role`` on every payload.
    """

    event_type: Literal["competitor.connected"] = "competitor.connected"
    agent_source: Literal["platform.api"] = "platform.api"

    channel_id: str = Field(description="Global Channel Registry UUID.")
    workspace_channel_id: str = Field(
        description="workspace_channels row UUID for the competitor binding.",
    )
    platform: str = Field(description="Channel platform (``telegram`` on MVP).")
    title: str | None = Field(
        default=None,
        description="Channel display name at connect time (snapshot).",
    )
    username: str | None = Field(
        default=None,
        description="``@handle`` without the leading ``@``; non-null for competitors.",
    )


class CompetitorDetachedEvent(BaseEvent):
    """A brand soft-detached a previously-connected competitor channel (PR #18).

    Mirrors :class:`ChannelDetachedEvent`. The audit trail and any
    cached competitor posts stay intact; the row in ``workspace_channels``
    just gets ``disconnected_at = now()``.
    """

    event_type: Literal["competitor.detached"] = "competitor.detached"
    agent_source: Literal["platform.api"] = "platform.api"

    channel_id: str = Field(description="Global Channel Registry UUID.")
    workspace_channel_id: str = Field(
        description="workspace_channels row UUID that was detached.",
    )
    platform: str = Field(description="Channel platform (``telegram`` on MVP).")


class BrandCreatedEvent(BaseEvent):
    """A new brand was just inserted into the workspace (PR #19).

    Emitted by ``POST /v1/brands`` after the row is committed. The
    SPA subscribes on the per-user channel and prepends the brand
    to the switcher dropdown so a second tab sees the new brand
    without a manual reload.
    """

    event_type: Literal["brand.created"] = "brand.created"
    agent_source: Literal["platform.api"] = "platform.api"

    name: str = Field(description="Human-readable brand name at create time.")
    content_language: str = Field(description="Brand content language (``ru``/``en``).")
    timezone: str = Field(description="Brand IANA timezone.")
    is_default: bool = Field(description="True when the new brand is the workspace's default.")


class BrandUpdatedEvent(BaseEvent):
    """A brand's metadata was patched (PR #19).

    ``changed_fields`` carries the keys that actually changed so a
    subscriber can ignore no-op repaints — the route handler builds
    the list from :func:`app.services.brands.update_brand`'s return
    value (only fields whose value differs from the previous row
    are listed).
    """

    event_type: Literal["brand.updated"] = "brand.updated"
    agent_source: Literal["platform.api"] = "platform.api"

    changed_fields: list[str] = Field(
        description=(
            "Subset of ``['name', 'content_language', 'timezone']`` that "
            "actually changed in this PATCH."
        ),
    )
    name: str = Field(description="Brand name after the PATCH.")
    content_language: str = Field(description="Brand content language after the PATCH.")
    timezone: str = Field(description="Brand timezone after the PATCH.")


class BrandDefaultChangedEvent(BaseEvent):
    """The workspace's default brand was swapped (PR #19).

    Emitted by ``POST /v1/brands/{id}/default``. Consumers (header
    brand-switcher, dashboard) react by re-fetching
    ``GET /v1/users/me/brands`` so the radio-flag flips in sync
    across every open tab.
    """

    event_type: Literal["brand.default_changed"] = "brand.default_changed"
    agent_source: Literal["platform.api"] = "platform.api"

    previous_default_brand_id: str | None = Field(
        default=None,
        description=(
            "Brand UUID that was the default before this call. ``None`` for "
            "workspaces that didn't have a default yet (legacy backfill path)."
        ),
    )


class BrandDeletedEvent(BaseEvent):
    """A brand was soft-deleted (PR #19).

    Emitted by ``DELETE /v1/brands/{id}``. Consumers drop the
    brand from the switcher dropdown; the SPA's active-brand store
    re-resolves to the workspace's new default if the deleted
    brand was the active one.
    """

    event_type: Literal["brand.deleted"] = "brand.deleted"
    agent_source: Literal["platform.api"] = "platform.api"


class AgentRunStartedEvent(BaseEvent):
    """A new ``agent_runs`` row was just persisted (PR #20).

    Published from :class:`~app.services.agent_run_writer.AgentRunWriter`
    immediately after ``start_run`` commits. Sprint-8 CostGuardian
    subscribes here to mark the workspace's "active run" gauge.
    """

    event_type: Literal["agent.run.started"] = "agent.run.started"
    agent_source: Literal["platform.agents"] = "platform.agents"

    agent_run_id: str = Field(description="agent_runs row UUID.")
    agent: str = Field(description="``agent_runs.agent`` value (e.g. ``healthcheck``).")
    agent_version: str = Field(description="``agent_runs.agent_version`` snapshot.")
    parent_run_id: str | None = Field(
        default=None,
        description="``agent_runs.parent_run_id`` UUID, or None for top-level runs.",
    )


class AgentRunFinishedEvent(BaseEvent):
    """An ``agent_runs`` row reached terminal status (PR #20).

    Published right after :meth:`AgentRunWriter.finish_run` commits.
    Carries the denormalised totals so consumers (CostGuardian,
    WS-toast notifier) don't have to re-query the row.
    """

    event_type: Literal["agent.run.finished"] = "agent.run.finished"
    agent_source: Literal["platform.agents"] = "platform.agents"

    agent_run_id: str = Field(description="agent_runs row UUID.")
    agent: str = Field(description="``agent_runs.agent`` value.")
    status: Literal["succeeded", "failed", "cancelled"] = Field(
        description="Terminal status assigned by ``finish_run``.",
    )
    latency_ms: int = Field(
        default=0,
        ge=0,
        description="``finished_at - started_at`` in milliseconds.",
    )
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    cost_usd: str = Field(
        default="0",
        description="Denormalised ``agent_runs.cost_usd`` serialised as a string (Decimal-safe).",
    )
    cost_rub: str = Field(
        default="0",
        description="Denormalised ``agent_runs.cost_rub`` serialised as a string.",
    )
    error_code: str | None = Field(
        default=None,
        description="``agent_runs.error_code`` on failure, otherwise ``None``.",
    )


class LLMCallFailedEvent(BaseEvent):
    """One ``llm_calls`` row landed with ``success=false`` (PR #20).

    Published after a non-recoverable LLM error inside a tool-calling
    loop. Sprint-8 CostGuardian uses the per-provider failure rate
    to drive the auto-downgrade ladder (D59 / D60 in docs/04 §16.6).
    """

    event_type: Literal["llm.call.failed"] = "llm.call.failed"
    agent_source: Literal["platform.agents"] = "platform.agents"

    agent_run_id: str = Field(description="parent agent_runs row UUID.")
    llm_call_id: str = Field(description="llm_calls row UUID.")
    provider: str = Field(description="``llm_calls.provider``.")
    model: str = Field(description="``llm_calls.model``.")
    error_code: str = Field(
        description="Typed ``LLMError.error_code`` (e.g. ``LLM_TIMEOUT``).",
    )
    retries: int = Field(
        default=0,
        ge=0,
        description="``llm_calls.retries`` snapshot at the moment of failure.",
    )


class CircuitBreakerOpenedEvent(BaseEvent):
    """A per-(provider, model) circuit breaker just transitioned to OPEN.

    Published once per state transition (CLOSED→OPEN or HALF_OPEN→OPEN)
    by :class:`~app.adapters.llm.circuit_breaker.LLMCircuitBreaker`.
    No matching ``circuit_breaker.closed`` event yet — the recovery
    path is wired in Sprint 8 alongside the on-call dashboard.
    """

    event_type: Literal["circuit_breaker.opened"] = "circuit_breaker.opened"
    agent_source: Literal["platform.agents"] = "platform.agents"

    provider: str = Field(description="LLM provider slug (e.g. ``polza``).")
    model: str = Field(description="Model that tripped the breaker.")
    reason: str = Field(
        description=(
            "Free-form slug — typically ``LLMError.error_code`` of the failing call "
            "(``LLM_PROVIDER_UNAVAILABLE``, ``LLM_TIMEOUT``, …)."
        ),
    )
    fail_count: int = Field(
        ge=0,
        description="Number of consecutive failures observed before opening.",
    )


class BrandMemoryCoreUpdatedEvent(BaseEvent):
    """The brand's core memory payload was just PATCH'd (PR #21).

    Emitted by ``PATCH /v1/brands/{id}/memory/core`` after the row
    commits. Subscribers:

    * the SPA — invalidates its TanStack-Query cache for
      ``brand-memory-core`` on the user's per-user channel so a
      second tab sees the new payload without a manual reload;
    * the Content Agent (PR #25) — invalidates its in-process
      memoised effective-payload entry so the next draft pulls the
      fresh values.

    The payload itself is intentionally *not* embedded — events are
    notification-only and consumers re-fetch via the API so RLS is
    re-applied and the wire-format stays small.
    """

    event_type: Literal["brand_memory.core_updated"] = "brand_memory.core_updated"
    agent_source: Literal["platform.api"] = "platform.api"

    version: int = Field(
        ge=1,
        description="``brand_memory_core.version`` after the PATCH committed.",
    )
    updated_by_agent: str | None = Field(
        default=None,
        description=(
            "Slug of the agent that applied the PATCH (e.g. ``onboarding``), "
            "or None when a human operator edited via the SPA."
        ),
    )


class BrandMemoryOverlayUpdatedEvent(BaseEvent):
    """A per-channel Brand Memory overlay was just PATCH'd (PR #21).

    Mirrors :class:`BrandMemoryCoreUpdatedEvent`. The ``workspace_channel_id``
    field lets channel-scoped consumers (Publisher agent's draft
    cache for that channel) skip events that don't affect them.
    """

    event_type: Literal["brand_memory.overlay_updated"] = "brand_memory.overlay_updated"
    agent_source: Literal["platform.api"] = "platform.api"

    workspace_channel_id: str = Field(
        description="``workspace_channels.id`` the overlay belongs to.",
    )
    version: int = Field(
        ge=1,
        description="``brand_memory_overlays.version`` after the PATCH committed.",
    )
    updated_by_agent: str | None = Field(
        default=None,
        description=("Slug of the agent that applied the PATCH, or None for human edits."),
    )


class ChannelPostEditedEvent(BaseEvent):
    """A previously-ingested channel post was edited on Telegram (PR #16).

    Emitted by the webhook ingestor when an ``edited_channel_post``
    update lands for a known ``(channel_id, tg_message_id)`` pair.
    The corresponding row in ``channel_posts`` is upserted with the
    new ``text`` / ``entities`` / ``media_summary`` / ``views_count``
    before the event is published, so consumers that re-read the row
    see the latest revision.

    Live edits are rare in the typical channel lifecycle (typos /
    legal corrections) but matter for the Brand Memory updater: a
    rewritten post invalidates the previous embedding and forces a
    re-summarize pass.
    """

    event_type: Literal["channel.post_edited"] = "channel.post_edited"
    agent_source: Literal["agent.publisher"] = "agent.publisher"

    channel_id: str = Field(description="Global Channel Registry UUID.")
    workspace_channel_id: str = Field(
        description="workspace_channels row UUID the post belongs to.",
    )
    channel_post_id: str = Field(description="channel_posts row UUID (unchanged across edits).")
    tg_message_id: int = Field(description="Telegram ``message_id`` (dedup key, unchanged).")
    posted_at: datetime = Field(
        description="UTC timestamp the post was originally published (unchanged).",
    )
    edited_at: datetime = Field(
        description=(
            "UTC timestamp of the most recent edit — equals ``Message.edit_date`` from the Bot API."
        ),
    )
    has_media: bool = Field(
        default=False,
        description="True if the (possibly-edited) post now carries media.",
    )


# Discriminated union — every new event-type subclass goes here.
Event = Annotated[
    UserRegisteredEvent
    | AuthRefreshRequiredEvent
    | ChannelConnectedEvent
    | ChannelDetachedEvent
    | ChannelBackfillStartedEvent
    | ChannelBackfillCompletedEvent
    | ChannelPostReceivedEvent
    | ChannelPostEditedEvent
    | CompetitorConnectedEvent
    | CompetitorDetachedEvent
    | BrandCreatedEvent
    | BrandUpdatedEvent
    | BrandDefaultChangedEvent
    | BrandDeletedEvent
    | BrandMemoryCoreUpdatedEvent
    | BrandMemoryOverlayUpdatedEvent
    | AgentRunStartedEvent
    | AgentRunFinishedEvent
    | LLMCallFailedEvent
    | CircuitBreakerOpenedEvent,
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
