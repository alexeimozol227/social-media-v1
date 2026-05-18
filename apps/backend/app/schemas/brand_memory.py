"""Brand Memory API request / response schemas (PR #21).

docs/plans/phase1-sprint3-plan.md §"PR #21 — Brand Memory v0":

* :class:`BrandMemoryPayload` — closed-vocabulary projection of the
  JSONB blob stored in :class:`app.models.brand_memory.BrandMemoryCore.payload`.
  Used for both validating PATCH bodies and serialising GET responses.
* :class:`UpdateBrandMemoryCoreRequest` /
  :class:`UpdateBrandMemoryOverlayRequest` — bodies for ``PATCH
  /v1/brands/{id}/memory/core`` and ``PATCH .../overlays/{ws_channel_id}``.
  Both carry an optional ``if_match_version`` for optimistic
  concurrency.
* :class:`BrandMemoryCoreView` / :class:`BrandMemoryOverlayView` /
  :class:`EffectiveBrandMemoryView` — GET payload projections.
* :class:`BrandMemoryExampleView` / :class:`BrandMemoryExampleList` —
  list response for ``GET /v1/brands/{id}/memory/examples``.

The schema is intentionally conservative for PR #21:

* every top-level key is closed-vocabulary (``extra='forbid'``);
* list fields are length-capped + per-item char-capped so a malicious
  caller can't park megabytes of JSONB inside a brand;
* string fields are length-capped.

Sprint-3 PR #22 (OnboardingAgent) and PR #25 (Content Agent) extend
the surface by adding new payload keys; bumping ``BrandMemoryPayload``
is the contract-evolution point.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

# ---------------------------------------------------------------------------
# Payload sub-schemas
# ---------------------------------------------------------------------------

_SHORT_STR = Annotated[str, StringConstraints(min_length=1, max_length=255, strip_whitespace=True)]
_LONG_STR = Annotated[str, StringConstraints(min_length=1, max_length=2048, strip_whitespace=True)]
_PHRASE_STR = Annotated[str, StringConstraints(min_length=1, max_length=128, strip_whitespace=True)]
_TAG_STR = Annotated[str, StringConstraints(min_length=1, max_length=64, strip_whitespace=True)]


class ToneOfVoice(BaseModel):
    """Brand voice descriptor (one of the canonical Brand Memory facets).

    Each field is optional so the SPA can build up the description
    incrementally during onboarding. The OnboardingAgent (PR #22)
    populates ``voice`` + ``style_keywords`` directly from the
    channel-history embedding pass.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    voice: _SHORT_STR | None = Field(
        default=None,
        description="One-line voice descriptor, e.g. ``friendly expert``.",
    )
    style_keywords: list[_TAG_STR] = Field(
        default_factory=list,
        max_length=32,
        description="Short tags (max 32) characterising the brand's writing style.",
    )
    formality: Annotated[str, StringConstraints(max_length=32)] | None = Field(
        default=None,
        description="Free-form formality slug (``casual`` / ``professional`` / …).",
    )
    emoji_policy: Annotated[str, StringConstraints(max_length=32)] | None = Field(
        default=None,
        description="``none`` / ``light`` / ``moderate`` / ``heavy`` — guides emoji density.",
    )


class TargetAudience(BaseModel):
    """Target audience facet (B2B vs B2C, demographics, pains, etc.)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    description: _LONG_STR | None = Field(
        default=None,
        description="Free-form description of the brand's target audience.",
    )
    demographics: list[_PHRASE_STR] = Field(
        default_factory=list,
        max_length=16,
        description="Short demographic descriptors (e.g. ``B2B SaaS founders``).",
    )
    pains: list[_PHRASE_STR] = Field(
        default_factory=list,
        max_length=16,
        description="Pain points the brand addresses.",
    )
    goals: list[_PHRASE_STR] = Field(
        default_factory=list,
        max_length=16,
        description="Audience goals the brand helps achieve.",
    )


class PostFrequency(BaseModel):
    """Publishing cadence preferences (consumed by the Publisher agent)."""

    model_config = ConfigDict(extra="forbid")

    posts_per_week: Annotated[int, Field(ge=0, le=50)] | None = Field(
        default=None,
        description="Target posts per week (0..50). ``None`` means no preference.",
    )
    preferred_days: list[
        Annotated[
            str,
            StringConstraints(
                min_length=3,
                max_length=3,
                strip_whitespace=True,
                to_lower=True,
            ),
        ]
    ] = Field(
        default_factory=list,
        max_length=7,
        description="Three-letter weekday codes (``mon`` / ``tue`` / …).",
    )

    @field_validator("preferred_days")
    @classmethod
    def _validate_days(cls, value: list[str]) -> list[str]:
        allowed = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
        for day in value:
            if day not in allowed:
                msg = (
                    f"Unsupported weekday code: {day!r} "
                    "(expected one of: mon/tue/wed/thu/fri/sat/sun)"
                )
                raise ValueError(msg)
        # Dedup while preserving order so the SPA can store the same set
        # the user picked without surprise reorderings.
        seen: set[str] = set()
        out: list[str] = []
        for day in value:
            if day in seen:
                continue
            seen.add(day)
            out.append(day)
        return out


# ---------------------------------------------------------------------------
# Top-level payload
# ---------------------------------------------------------------------------


class BrandMemoryPayload(BaseModel):
    """Canonical Brand Memory payload (core + overlay share the shape).

    Every field is optional — a freshly-seeded brand starts with an
    empty payload and is filled in incrementally by the SPA / the
    OnboardingAgent. The shape stays identical across core and
    overlay because the service layer merges them shallowly: keys
    set on the overlay win, keys absent on the overlay fall through
    to the core.

    ``extras`` is the forward-compatibility hatch — Sprint 4+ agents
    can park their own keys here without bumping the payload schema.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    tone: ToneOfVoice | None = Field(
        default=None,
        description="Brand voice / style descriptor.",
    )
    audience: TargetAudience | None = Field(
        default=None,
        description="Target audience descriptor (demographics, pains, goals).",
    )
    taboos: list[_PHRASE_STR] = Field(
        default_factory=list,
        max_length=64,
        description="Phrases / topics the brand never publishes.",
    )
    post_types: list[_TAG_STR] = Field(
        default_factory=list,
        max_length=32,
        description="Categories of posts the brand publishes (``news`` / ``tip`` / …).",
    )
    post_frequency: PostFrequency | None = Field(
        default=None,
        description="Publishing cadence preferences.",
    )
    keywords: list[_TAG_STR] = Field(
        default_factory=list,
        max_length=64,
        description="Signature keywords / themes the brand wants to anchor on.",
    )
    extras: dict[
        Annotated[str, StringConstraints(min_length=1, max_length=64)],
        Annotated[str, StringConstraints(max_length=512)],
    ] = Field(
        default_factory=dict,
        description=(
            "Forward-compat free-form key/value strings. Capped at 64-char keys "
            "and 512-char values per entry; the service layer caps the dict to "
            "32 entries."
        ),
    )

    @model_validator(mode="after")
    def _bound_extras(self) -> BrandMemoryPayload:
        if len(self.extras) > 32:
            msg = "extras dict cannot carry more than 32 entries"
            raise ValueError(msg)
        return self


# ---------------------------------------------------------------------------
# PATCH bodies
# ---------------------------------------------------------------------------


class UpdateBrandMemoryCoreRequest(BaseModel):
    """Body of ``PATCH /v1/brands/{brand_id}/memory/core``.

    ``payload`` is the new core payload. PATCH replaces the row
    contents wholesale (PR #21 keeps semantics simple — partial-key
    merge lands in Sprint 4 alongside the SPA editor that needs it).

    ``if_match_version`` provides optimistic concurrency control:
    when set, the service rejects the PATCH with
    :class:`~app.errors.BrandMemoryVersionConflictError` if the
    stored row's ``version`` doesn't match.
    """

    model_config = ConfigDict(extra="forbid")

    payload: BrandMemoryPayload = Field(description="New canonical payload.")
    if_match_version: Annotated[int, Field(ge=1)] | None = Field(
        default=None,
        description=(
            "Optimistic-concurrency token; pass the ``version`` returned by "
            "the most recent GET. The PATCH 409s when the stored row's "
            "version differs."
        ),
    )


class UpdateBrandMemoryOverlayRequest(BaseModel):
    """Body of ``PATCH /v1/brands/{brand_id}/memory/overlays/{ws_channel_id}``.

    Same shape as :class:`UpdateBrandMemoryCoreRequest`. The service
    upserts the row, treating a missing row as "first-time write".
    """

    model_config = ConfigDict(extra="forbid")

    payload: BrandMemoryPayload = Field(description="New overlay payload.")
    if_match_version: Annotated[int, Field(ge=1)] | None = Field(
        default=None,
        description=(
            "Optimistic-concurrency token; pass the ``version`` returned by "
            "the most recent GET. The PATCH 409s when the stored row's "
            "version differs. Ignored on first-time inserts."
        ),
    )


# ---------------------------------------------------------------------------
# GET projections
# ---------------------------------------------------------------------------


class BrandMemoryCoreView(BaseModel):
    """``GET /v1/brands/{brand_id}/memory/core`` payload."""

    model_config = ConfigDict(from_attributes=True)

    brand_id: uuid.UUID = Field(description="Brand UUID.")
    payload: BrandMemoryPayload = Field(description="Canonical brand-wide payload.")
    version: Annotated[int, Field(ge=1)] = Field(
        description="Monotonic version, bumped on every successful PATCH.",
    )
    updated_by_user_id: uuid.UUID | None = Field(
        default=None,
        description="UUID of the user who applied the last PATCH (manual edit).",
    )
    updated_by_agent: Annotated[str | None, StringConstraints(max_length=64)] = Field(
        default=None,
        description="Slug of the agent that applied the last PATCH (e.g. ``onboarding``).",
    )
    created_at: datetime
    updated_at: datetime


class BrandMemoryOverlayView(BaseModel):
    """``GET /v1/brands/{brand_id}/memory/overlays/{ws_channel_id}`` payload."""

    model_config = ConfigDict(from_attributes=True)

    brand_id: uuid.UUID = Field(description="Brand UUID.")
    workspace_channel_id: uuid.UUID = Field(
        description="``workspace_channels.id`` the overlay belongs to.",
    )
    payload: BrandMemoryPayload = Field(description="Overlay payload (deltas over core).")
    version: Annotated[int, Field(ge=1)] = Field(
        description="Monotonic version, bumped on every successful PATCH.",
    )
    updated_by_user_id: uuid.UUID | None = Field(default=None)
    updated_by_agent: Annotated[str | None, StringConstraints(max_length=64)] = Field(default=None)
    created_at: datetime
    updated_at: datetime


class EffectiveBrandMemoryView(BaseModel):
    """``GET /v1/brands/{brand_id}/memory/effective`` payload (core + overlay merge).

    The service merges ``core.payload`` with the matching overlay's
    ``payload`` shallowly — keys present on the overlay win, missing
    keys fall through to the core. Consumers (Content Agent) read
    this projection directly.
    """

    brand_id: uuid.UUID = Field(description="Brand UUID.")
    workspace_channel_id: uuid.UUID | None = Field(
        default=None,
        description="``workspace_channels.id`` of the overlay applied, ``None`` for core-only.",
    )
    payload: BrandMemoryPayload = Field(description="Effective payload after merging.")
    core_version: Annotated[int, Field(ge=1)] = Field(
        description="``brand_memory_core.version`` at merge time.",
    )
    overlay_version: Annotated[int, Field(ge=1)] | None = Field(
        default=None,
        description="``brand_memory_overlays.version`` at merge time, or None if no overlay.",
    )


# ---------------------------------------------------------------------------
# Examples list
# ---------------------------------------------------------------------------


class BrandMemoryExampleView(BaseModel):
    """One row in the brand's examples list."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(description="``brand_memory_examples.id``.")
    brand_id: uuid.UUID = Field(description="Owning brand UUID.")
    source_channel_post_id: uuid.UUID | None = Field(
        default=None,
        description="``channel_posts.id`` the snippet was extracted from (or None).",
    )
    model: Annotated[str, StringConstraints(max_length=64)] = Field(
        description="Embedding model identifier (e.g. ``polza:text-embedding-3-small``).",
    )
    text_snippet: Annotated[str, StringConstraints(max_length=4096)] = Field(
        description="Truncated brand-post text (up to 4 KB).",
    )
    created_at: datetime


class BrandMemoryExampleList(BaseModel):
    """``GET /v1/brands/{brand_id}/memory/examples`` payload."""

    items: list[BrandMemoryExampleView] = Field(default_factory=list)
    total: int = Field(ge=0, description="Total number of examples available for the brand.")


__all__ = [
    "BrandMemoryCoreView",
    "BrandMemoryExampleList",
    "BrandMemoryExampleView",
    "BrandMemoryOverlayView",
    "BrandMemoryPayload",
    "EffectiveBrandMemoryView",
    "PostFrequency",
    "TargetAudience",
    "ToneOfVoice",
    "UpdateBrandMemoryCoreRequest",
    "UpdateBrandMemoryOverlayRequest",
]
