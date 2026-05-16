"""Brand business logic.

docs/04-architecture.md §3 + docs/plans/phase1-sprint2-plan.md
(PR #14 + PR #19): the brand is the unit every business object hangs
off (channels, content plan, posts).

Read-side helpers (PR #14):

* :func:`default_for_workspace` — returns the workspace's canonical
  brand (``brands.is_default = TRUE``), backfilling the flag on the
  oldest brand of a legacy workspace that doesn't have a default
  yet.
* :func:`get_in_workspace` — strict tenant-scoped lookup; returns
  ``None`` when ``brand_id`` doesn't belong to ``workspace_id`` so
  the API layer can map that to a typed
  :class:`app.errors.BrandNotInWorkspaceError`.
* :func:`list_for_workspace` — every brand the workspace owns,
  ordered with default first (handy for the brand-switcher UI).
* :func:`count_for_workspace` — non-soft-deleted brand count, used
  by the quota gate + ``BrandQuotaView.used_brands``.

Write-side helpers (PR #19):

* :func:`create_brand` — quota-gated INSERT with default-flag
  bookkeeping.
* :func:`update_brand` — PATCH-style field updates.
* :func:`set_default` — atomic default-brand swap honouring the
  partial unique index ``ux_brands_workspace_default``.
* :func:`delete_brand` — soft-delete with default / last-brand
  guard rails.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import (
    BrandDeleteDefaultBlockedError,
    BrandDeleteLastBlockedError,
    BrandNameRequiredError,
    BrandQuotaExceededError,
)
from app.models.brand import Brand

logger = structlog.get_logger(__name__)


def _now() -> datetime:
    return datetime.now(tz=UTC)


async def default_for_workspace(
    session: AsyncSession,
    workspace_id: uuid.UUID,
) -> Brand | None:
    """Return the workspace's default brand, repairing the flag if missing.

    Legacy workspaces created before PR #14 didn't have
    ``is_default`` — to keep that path correct we fall back to the
    oldest brand and flip its flag so subsequent calls return
    instantly. Brand-new workspaces created on or after PR #14
    already get ``is_default=True`` from
    :func:`app.services.workspaces.ensure_default`.

    Returns ``None`` only if the workspace has zero brands, which
    should never happen for a workspace that finished sign-up
    bootstrap.
    """

    res = await session.execute(
        select(Brand)
        .where(
            Brand.workspace_id == workspace_id,
            Brand.deleted_at.is_(None),
            Brand.is_default.is_(True),
        )
        .order_by(Brand.created_at.asc())
        .limit(1),
    )
    found = res.scalar_one_or_none()
    if found is not None:
        return found

    # Backfill path: pick the oldest brand and promote it. The
    # partial-unique index ``ux_brands_workspace_default`` makes
    # this a single safe write per workspace.
    res = await session.execute(
        select(Brand)
        .where(
            Brand.workspace_id == workspace_id,
            Brand.deleted_at.is_(None),
        )
        .order_by(Brand.created_at.asc())
        .limit(1),
    )
    oldest = res.scalar_one_or_none()
    if oldest is None:
        return None
    oldest.is_default = True
    await session.flush()
    logger.info(
        "brands.default_backfilled",
        workspace_id=str(workspace_id),
        brand_id=str(oldest.id),
    )
    return oldest


async def get_in_workspace(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    brand_id: uuid.UUID,
) -> Brand | None:
    """Strict tenant-scoped lookup.

    Returns ``None`` when ``brand_id`` doesn't belong to
    ``workspace_id`` (or doesn't exist). Caller maps that to a
    typed 403 — keeping the same response shape whether the brand
    is missing, soft-deleted, or owned by someone else, so a
    probe can't enumerate brand ids across workspaces.
    """

    res = await session.execute(
        select(Brand).where(
            Brand.id == brand_id,
            Brand.workspace_id == workspace_id,
            Brand.deleted_at.is_(None),
        ),
    )
    return res.scalar_one_or_none()


async def list_for_workspace(
    session: AsyncSession,
    workspace_id: uuid.UUID,
) -> list[Brand]:
    """Every brand the workspace owns, default-first then by ``created_at``.

    Used by the header brand-switcher UI (``GET /v1/users/me/brands``)
    and the admin lens.
    """

    res = await session.execute(
        select(Brand)
        .where(
            Brand.workspace_id == workspace_id,
            Brand.deleted_at.is_(None),
        )
        .order_by(Brand.is_default.desc(), Brand.created_at.asc()),
    )
    return list(res.scalars().all())


async def count_for_workspace(
    session: AsyncSession,
    workspace_id: uuid.UUID,
) -> int:
    """Return the number of non-soft-deleted brands in ``workspace_id``.

    Drives both :class:`~app.schemas.brands.BrandQuotaView.used_brands`
    and the :func:`create_brand` quota gate.
    """

    res = await session.execute(
        select(func.count(Brand.id)).where(
            Brand.workspace_id == workspace_id,
            Brand.deleted_at.is_(None),
        ),
    )
    return int(res.scalar_one() or 0)


# ---------------------------------------------------------------------------
# Write-side helpers (PR #19)
# ---------------------------------------------------------------------------


def _normalise_name(name: str) -> str:
    """Strip + reject blank brand names.

    Pydantic already trims + checks ``min_length=1``; the service-layer
    re-check is defensive against direct service calls (Celery tasks
    in future sprints) bypassing the schema.
    """

    cleaned = (name or "").strip()
    if not cleaned:
        raise BrandNameRequiredError()
    return cleaned


async def _reset_default_flag(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    except_brand_id: uuid.UUID | None,
) -> None:
    """Demote every brand in ``workspace_id`` whose id isn't ``except_brand_id``.

    The partial unique index ``ux_brands_workspace_default``
    enforces "≤1 default per workspace at a time" — this helper
    is the canonical way to flip the flag without violating it.
    """

    where = [
        Brand.workspace_id == workspace_id,
        Brand.deleted_at.is_(None),
        Brand.is_default.is_(True),
    ]
    if except_brand_id is not None:
        where.append(Brand.id != except_brand_id)
    await session.execute(
        update(Brand).where(*where).values(is_default=False),
    )
    await session.flush()


async def create_brand(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    name: str,
    content_language: str = "ru",
    timezone: str = "Europe/Minsk",
    is_default: bool = False,
    max_brands: int,
) -> Brand:
    """Insert a brand row, gated by ``max_brands`` and partial-unique invariant.

    Order of operations:

    1. Strip / re-validate ``name`` (defensive against direct calls).
    2. Count non-deleted brands in ``workspace_id``.
       * ``>= max_brands`` → :class:`BrandQuotaExceededError` (402).
    3. If ``is_default=True`` OR this is the workspace's first brand,
       demote every other ``is_default=TRUE`` row in the workspace so
       the new INSERT doesn't trip ``ux_brands_workspace_default``.
    4. INSERT the row. The flag follows the rule:
       * caller passed ``is_default=True`` → keep it,
       * first brand in workspace → force ``is_default=True``,
       * otherwise leave it ``False``.
    """

    cleaned_name = _normalise_name(name)
    current = await count_for_workspace(session, workspace_id)
    if current >= max_brands:
        raise BrandQuotaExceededError(
            suggested_action="upgrade_plan",
            details={
                "used_brands": current,
                "max_brands": max_brands,
            },
        )

    is_first_brand = current == 0
    effective_default = is_default or is_first_brand

    if effective_default:
        # Clear the previous default _before_ the INSERT so the
        # partial unique index doesn't trip mid-transaction.
        await _reset_default_flag(
            session,
            workspace_id=workspace_id,
            except_brand_id=None,
        )

    brand = Brand(
        workspace_id=workspace_id,
        name=cleaned_name,
        content_language=content_language,
        timezone=timezone,
        is_default=effective_default,
        disabled_global_skills=[],
    )
    session.add(brand)
    await session.flush()
    logger.info(
        "brands.created",
        workspace_id=str(workspace_id),
        brand_id=str(brand.id),
        is_default=effective_default,
        forced_default=is_first_brand and not is_default,
    )
    return brand


async def update_brand(
    session: AsyncSession,
    *,
    brand: Brand,
    name: str | None = None,
    content_language: str | None = None,
    timezone: str | None = None,
) -> tuple[Brand, list[str]]:
    """Apply a PATCH-style update and return ``(brand, changed_fields)``.

    ``is_default`` is intentionally out of scope: changing the default
    brand is a dedicated endpoint that goes through :func:`set_default`
    so the partial unique index is never briefly violated.

    Returns the list of fields that actually changed so the event-bus
    publisher can attach the diff to ``BrandUpdatedEvent``.
    """

    changed: list[str] = []
    if name is not None:
        cleaned = _normalise_name(name)
        if cleaned != brand.name:
            brand.name = cleaned
            changed.append("name")
    if content_language is not None and content_language != brand.content_language:
        brand.content_language = content_language
        changed.append("content_language")
    if timezone is not None and timezone != brand.timezone:
        brand.timezone = timezone
        changed.append("timezone")
    if changed:
        await session.flush()
        logger.info(
            "brands.updated",
            workspace_id=str(brand.workspace_id),
            brand_id=str(brand.id),
            changed_fields=changed,
        )
    return brand, changed


async def set_default(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    brand: Brand,
) -> Brand:
    """Promote ``brand`` to the workspace's default in one transaction.

    The pair of writes:

    1. Demote every other ``is_default=TRUE`` row in the workspace.
    2. Set ``brand.is_default = True``.

    must happen in a single transaction because the partial unique
    index ``ux_brands_workspace_default`` only permits one TRUE row
    at a time. The route handler commits the transaction once the
    audit / event-bus writes have been buffered.
    """

    if brand.is_default and brand.deleted_at is None:
        # Idempotent: already the default. Skip the writes so the
        # audit trail doesn't accumulate no-op rows.
        logger.info(
            "brands.default_unchanged",
            workspace_id=str(workspace_id),
            brand_id=str(brand.id),
        )
        return brand

    await _reset_default_flag(
        session,
        workspace_id=workspace_id,
        except_brand_id=brand.id,
    )
    brand.is_default = True
    await session.flush()
    logger.info(
        "brands.default_changed",
        workspace_id=str(workspace_id),
        brand_id=str(brand.id),
    )
    return brand


async def delete_brand(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    brand: Brand,
) -> Brand:
    """Soft-delete ``brand`` after the default / last-brand guards.

    Rejects the call when:

    * ``brand`` is the workspace's only remaining brand —
      :class:`BrandDeleteLastBlockedError` (409). Every workspace
      must keep at least one brand because channels / posts hang
      off it.
    * ``brand.is_default=True`` AND the workspace has other brands —
      :class:`BrandDeleteDefaultBlockedError` (409). User has to
      flip the default flag first.

    On success the row's ``deleted_at`` is set to UTC now; the
    related ``workspace_channels`` / ``channel_posts`` rows are
    intentionally untouched (partial-detach is Sprint 3).
    """

    total = await count_for_workspace(session, workspace_id)
    if total <= 1:
        raise BrandDeleteLastBlockedError()
    if brand.is_default:
        raise BrandDeleteDefaultBlockedError(
            suggested_action="set_other_default_first",
        )
    brand.deleted_at = _now()
    await session.flush()
    logger.info(
        "brands.deleted",
        workspace_id=str(workspace_id),
        brand_id=str(brand.id),
    )
    return brand


__all__ = [
    "count_for_workspace",
    "create_brand",
    "default_for_workspace",
    "delete_brand",
    "get_in_workspace",
    "list_for_workspace",
    "set_default",
    "update_brand",
]
