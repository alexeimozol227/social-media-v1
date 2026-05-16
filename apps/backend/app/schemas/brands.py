"""Brand API request / response schemas (PR #19).

docs/plans/phase1-sprint2-plan.md §"PR #19 — Brand settings + Dashboard v0":

* :class:`CreateBrandRequest` — body of ``POST /v1/brands``.
* :class:`UpdateBrandRequest` — body of ``PATCH /v1/brands/{id}``
  with PATCH semantics (every field optional).
* :class:`BrandView` — public projection of :class:`app.models.brand.Brand`
  used by every CRUD endpoint.
* :class:`BrandQuotaView` — surfaces the workspace's effective quotas
  (plan baseline merged with any active
  :class:`~app.models.tenant_limit_override.TenantLimitOverride`).
* :class:`BrandDashboardChannelView` / :class:`BrandDashboardPostPreview`
  / :class:`BrandDashboardView` — the Brand Dashboard v0 payload
  (``GET /v1/brands/{id}/dashboard``) showing the latest 5 posts
  ingested by PR #15 / PR #16.

The brand-switcher endpoint (``GET /v1/users/me/brands``) keeps
using :class:`app.schemas.channels.BrandSummary` for backwards
compatibility; PR #19 endpoints return the richer :class:`BrandView`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

# Closed vocabularies — kept here so the validator can reject any
# language the i18n bundle doesn't ship yet. Extending the list is a
# one-line change once a new locale lands in ``apps/web/messages/``.
ContentLanguage = Literal["ru", "en"]


_BRAND_NAME = Annotated[str, StringConstraints(min_length=1, max_length=255, strip_whitespace=True)]
_CONTENT_LANGUAGE = Annotated[str, StringConstraints(max_length=16)]
_TIMEZONE = Annotated[str, StringConstraints(min_length=1, max_length=64, strip_whitespace=True)]


def _validate_timezone(value: str) -> str:
    """Reject IANA-invalid timezone strings before they hit the DB.

    ``zoneinfo.ZoneInfo`` raises :class:`ZoneInfoNotFoundError` on an
    unknown identifier; we surface it as a 422 so the SPA can render
    "Unknown timezone" inline.
    """

    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        msg = f"Unknown IANA timezone: {value!r}"
        raise ValueError(msg) from exc
    return value


class CreateBrandRequest(BaseModel):
    """Body of ``POST /v1/brands``.

    ``content_language`` and ``timezone`` default to the workspace
    defaults (``ru`` / ``Europe/Minsk``) so the SPA can submit a
    bare ``{name}`` and let the backend fill the rest.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: _BRAND_NAME = Field(description="Human-readable brand name (1..255 chars).")
    content_language: _CONTENT_LANGUAGE = Field(
        default="ru",
        description=(
            "Two-letter language code used by the content agent when "
            "drafting posts. ``ru`` / ``en`` on MVP."
        ),
    )
    timezone: _TIMEZONE = Field(
        default="Europe/Minsk",
        description="IANA timezone used by the publisher for scheduling.",
    )
    is_default: bool = Field(
        default=False,
        description=(
            "If true, the new brand becomes the workspace's default brand "
            "(previous default is demoted in the same transaction)."
        ),
    )

    @field_validator("content_language")
    @classmethod
    def _validate_content_language(cls, value: str) -> str:
        if value not in ("ru", "en"):
            msg = f"Unsupported content_language: {value!r} (expected one of: ru, en)"
            raise ValueError(msg)
        return value

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, value: str) -> str:
        return _validate_timezone(value)


class UpdateBrandRequest(BaseModel):
    """Body of ``PATCH /v1/brands/{id}`` — every field is optional.

    ``is_default`` is intentionally omitted: changing the default
    brand is a dedicated endpoint (``POST /v1/brands/{id}/default``)
    so the caller never sends a "delete + set default" combo that
    would briefly violate the partial unique index in mid-transaction.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: _BRAND_NAME | None = Field(default=None)
    content_language: _CONTENT_LANGUAGE | None = Field(default=None)
    timezone: _TIMEZONE | None = Field(default=None)

    @field_validator("content_language")
    @classmethod
    def _validate_content_language(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if value not in ("ru", "en"):
            msg = f"Unsupported content_language: {value!r} (expected one of: ru, en)"
            raise ValueError(msg)
        return value

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _validate_timezone(value)

    @model_validator(mode="after")
    def _at_least_one_field(self) -> UpdateBrandRequest:
        # A PATCH with an empty body is almost certainly a frontend
        # bug; we reject it as 422 rather than silently no-op so the
        # caller notices.
        if self.name is None and self.content_language is None and self.timezone is None:
            msg = "PATCH body must include at least one of: name, content_language, timezone"
            raise ValueError(msg)
        return self


class BrandView(BaseModel):
    """Public projection of :class:`app.models.brand.Brand`.

    Mirrors every column the SPA needs to render the brand row /
    edit form. ``disabled_global_skills`` is exposed for forward
    compatibility (Sprint 4 surfaces the toggles UI) but PR #19 only
    ever returns an empty list.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(description="Brand UUID.")
    workspace_id: uuid.UUID = Field(description="Owning workspace UUID.")
    name: Annotated[str, StringConstraints(max_length=255)]
    content_language: Annotated[str, StringConstraints(max_length=16)]
    timezone: Annotated[str, StringConstraints(max_length=64)]
    is_default: bool = Field(description="True for the workspace's canonical brand.")
    disabled_global_skills: list[str] = Field(
        default_factory=list,
        description=(
            "Global skill keys explicitly disabled on this brand. Empty on "
            "PR #19 — the toggles UI ships in Sprint 4."
        ),
    )
    created_at: datetime
    updated_at: datetime


class BrandQuotaView(BaseModel):
    """Effective quota snapshot for ``GET /v1/brands/quota``.

    Surfaces :class:`app.services.billing.quotas.EffectiveLimits`
    alongside the plan metadata + current brand count so the
    ``/settings/brands`` page can render "X / Y brands on plan Z"
    without a second round-trip.
    """

    plan_id: uuid.UUID = Field(description="Active plan UUID.")
    plan_code: str | None = Field(default=None, description="Plan code (e.g. ``solo`` / ``pro``).")
    plan_name: str | None = Field(default=None, description="Plan display name.")
    max_brands: int = Field(description="Effective brand ceiling for the workspace.")
    used_brands: int = Field(description="Brands currently allocated (excludes soft-deleted).")
    max_posts_per_month: int = Field(description="Effective post ceiling per month.")
    max_channels_per_brand: int = Field(description="Plan-level channel ceiling per brand.")
    max_competitors: int = Field(description="Plan-level competitor ceiling per brand.")
    override_active: bool = Field(
        description=(
            "True when at least one quota column was sourced from an "
            "active :class:`TenantLimitOverride` row (admin VIP / promo)."
        ),
    )


class BrandDashboardChannelView(BaseModel):
    """Lightweight projection of the brand's main owned channel.

    Returned as part of :class:`BrandDashboardView`. ``None`` when
    the brand has no active owned channel yet.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(description="``workspace_channels.id`` of the binding.")
    channel_id: uuid.UUID = Field(description="``channels.id`` (Global Channel Registry).")
    title: Annotated[str | None, StringConstraints(max_length=255)] = Field(default=None)
    username: Annotated[str | None, StringConstraints(max_length=64)] = Field(default=None)
    role: str = Field(description="``owned`` for the brand's main channel.")
    subscribers_count: int | None = Field(default=None)
    connected_at: datetime


class BrandDashboardPostPreview(BaseModel):
    """One ``channel_posts`` row trimmed to the dashboard preview shape."""

    id: uuid.UUID = Field(description="``channel_posts.id``.")
    tg_message_id: int = Field(description="Telegram ``message_id`` (dedup key).")
    text_preview: str | None = Field(
        default=None,
        description=(
            "First 200 chars of the post body (with trailing ellipsis when "
            "truncated). ``None`` for media-only posts with no caption."
        ),
    )
    has_media: bool = Field(default=False)
    posted_at: datetime
    views_count: int | None = Field(default=None)


class BrandDashboardView(BaseModel):
    """Payload returned by ``GET /v1/brands/{brand_id}/dashboard``.

    ``status`` is the discriminator the SPA pivots on:

    * ``ok`` — channel + at least one post; render the carousel.
    * ``no_active_channel`` — render the "Connect a channel" CTA.
    * ``no_posts_yet`` — channel is connected but the ingest pipeline
      hasn't written any posts yet; render the "Waiting for the first
      post…" skeleton.
    """

    status: Literal["ok", "no_active_channel", "no_posts_yet"]
    channel: BrandDashboardChannelView | None = Field(default=None)
    recent_posts: list[BrandDashboardPostPreview] = Field(default_factory=list)


__all__ = [
    "BrandDashboardChannelView",
    "BrandDashboardPostPreview",
    "BrandDashboardView",
    "BrandQuotaView",
    "BrandView",
    "CreateBrandRequest",
    "UpdateBrandRequest",
]
