"""Brand Memory service (PR #21).

docs/plans/phase1-sprint3-plan.md §"PR #21 — Brand Memory v0" + П11
(single source of truth invariant): mediates between the route layer
and the three ``brand_memory_*`` tables, layering a 5-minute Redis
cache + cache-invalidation on top of the SQL writes.

Public surface
--------------
* :func:`get_core` — fetch (cache-then-DB) the canonical core row.
* :func:`update_core` — version-checked PATCH; bumps ``version``,
  invalidates the Redis cache.
* :func:`get_overlay` — fetch the per-channel overlay (or ``None``).
* :func:`update_overlay` — upsert an overlay row; version-checked
  when one already exists.
* :func:`get_effective` — merge ``core.payload`` with the matching
  overlay shallowly so the Content Agent reads "effective settings"
  in one call.
* :func:`list_examples` — paginated list of brand-memory example rows.
* :func:`invalidate_*` — manual cache busters (called by the SPA
  /admin lens after destructive ops, and by the routes themselves on
  every successful PATCH).

Cache contract (docs/04 §8 + docs/05 §6.6, mirrors
``memberships_cache`` from PR #14):

* ``bm:core:{brand_id}`` — JSON-encoded ``(payload, version,
  updated_at_iso)``;  TTL :data:`BRAND_MEMORY_CACHE_TTL_SECONDS`.
* ``bm:overlay:{brand_id}:{workspace_channel_id}`` — same shape.

The cache is a latency optimisation, not the source of truth — a
Redis blip falls through to a single ``SELECT`` against the
authoritative table. Writes ALWAYS invalidate the cache (a stale
read for up to 5 minutes is acceptable on a Redis-down day but a
stale read after a manual edit is not).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import (
    BrandMemoryChannelNotBoundError,
    BrandMemoryInvalidPayloadError,
    BrandMemoryVersionConflictError,
)
from app.models.brand_memory import (
    BrandMemoryCore,
    BrandMemoryExample,
    BrandMemoryOverlay,
)
from app.models.channel import WorkspaceChannel

logger = structlog.get_logger(__name__)


# 5-minute TTL mirrors :data:`memberships_cache.MEMBERSHIP_CACHE_TTL_SECONDS`.
# Bumping this stretches the staleness window for *Redis-down* reads only;
# every PATCH ``DELETE``s the matching keys synchronously inside the
# request so the SPA never sees its own write as stale.
BRAND_MEMORY_CACHE_TTL_SECONDS = 300


# Hard cap on ``list_examples`` page sizes — keeps the SPA-side virtual
# scroller bounded without forcing the route layer to declare another
# config knob.
LIST_EXAMPLES_MAX_LIMIT = 200


# ---------------------------------------------------------------------------
# Cache keys
# ---------------------------------------------------------------------------


def _core_cache_key(brand_id: uuid.UUID | str) -> str:
    return f"bm:core:{brand_id}"


def _overlay_cache_key(
    brand_id: uuid.UUID | str,
    workspace_channel_id: uuid.UUID | str,
) -> str:
    return f"bm:overlay:{brand_id}:{workspace_channel_id}"


# ---------------------------------------------------------------------------
# Result envelopes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CoreSnapshot:
    """In-memory projection of a :class:`BrandMemoryCore` row.

    Sourced either from Redis (cache hit) or the DB (cache miss / blip).
    The route layer maps this onto :class:`~app.schemas.brand_memory.BrandMemoryCoreView`
    before returning it.
    """

    brand_id: uuid.UUID
    payload: dict[str, Any]
    version: int
    updated_by_user_id: uuid.UUID | None
    updated_by_agent: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class OverlaySnapshot:
    """In-memory projection of a :class:`BrandMemoryOverlay` row.

    Same shape as :class:`CoreSnapshot` plus ``workspace_channel_id``.
    The route layer maps it onto
    :class:`~app.schemas.brand_memory.BrandMemoryOverlayView`.
    """

    brand_id: uuid.UUID
    workspace_channel_id: uuid.UUID
    payload: dict[str, Any]
    version: int
    updated_by_user_id: uuid.UUID | None
    updated_by_agent: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class EffectiveMemory:
    """Merged core + overlay payload returned by :func:`get_effective`.

    ``workspace_channel_id`` is ``None`` when no overlay was found
    (effective payload === core payload). ``overlay_version`` is
    correspondingly ``None`` in that case; the SPA / Content Agent
    use both versions to keep their own caches in sync.
    """

    brand_id: uuid.UUID
    workspace_channel_id: uuid.UUID | None
    payload: dict[str, Any]
    core_version: int
    overlay_version: int | None


# ---------------------------------------------------------------------------
# DB-side helpers
# ---------------------------------------------------------------------------


async def _load_core_row(
    session: AsyncSession,
    *,
    brand_id: uuid.UUID,
) -> BrandMemoryCore | None:
    res = await session.execute(
        select(BrandMemoryCore).where(BrandMemoryCore.brand_id == brand_id),
    )
    return res.scalar_one_or_none()


async def _load_overlay_row(
    session: AsyncSession,
    *,
    brand_id: uuid.UUID,
    workspace_channel_id: uuid.UUID,
) -> BrandMemoryOverlay | None:
    res = await session.execute(
        select(BrandMemoryOverlay).where(
            BrandMemoryOverlay.brand_id == brand_id,
            BrandMemoryOverlay.workspace_channel_id == workspace_channel_id,
        ),
    )
    return res.scalar_one_or_none()


def _core_to_snapshot(row: BrandMemoryCore) -> CoreSnapshot:
    return CoreSnapshot(
        brand_id=row.brand_id,
        payload=dict(row.payload or {}),
        version=int(row.version),
        updated_by_user_id=row.updated_by_user_id,
        updated_by_agent=row.updated_by_agent,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _overlay_to_snapshot(row: BrandMemoryOverlay) -> OverlaySnapshot:
    return OverlaySnapshot(
        brand_id=row.brand_id,
        workspace_channel_id=row.workspace_channel_id,
        payload=dict(row.payload or {}),
        version=int(row.version),
        updated_by_user_id=row.updated_by_user_id,
        updated_by_agent=row.updated_by_agent,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# ---------------------------------------------------------------------------
# Cache (de)serialisation
# ---------------------------------------------------------------------------


def _encode_core_cache(snap: CoreSnapshot) -> str:
    return json.dumps(
        {
            "brand_id": str(snap.brand_id),
            "payload": snap.payload,
            "version": snap.version,
            "updated_by_user_id": (
                str(snap.updated_by_user_id) if snap.updated_by_user_id else None
            ),
            "updated_by_agent": snap.updated_by_agent,
            "created_at": snap.created_at.isoformat(),
            "updated_at": snap.updated_at.isoformat(),
        },
        default=str,
    )


def _decode_core_cache(raw: str) -> CoreSnapshot | None:
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        return CoreSnapshot(
            brand_id=uuid.UUID(data["brand_id"]),
            payload=dict(data.get("payload") or {}),
            version=int(data["version"]),
            updated_by_user_id=(
                uuid.UUID(data["updated_by_user_id"]) if data.get("updated_by_user_id") else None
            ),
            updated_by_agent=data.get("updated_by_agent"),
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
        )
    except (KeyError, ValueError, TypeError):
        return None


def _encode_overlay_cache(snap: OverlaySnapshot) -> str:
    return json.dumps(
        {
            "brand_id": str(snap.brand_id),
            "workspace_channel_id": str(snap.workspace_channel_id),
            "payload": snap.payload,
            "version": snap.version,
            "updated_by_user_id": (
                str(snap.updated_by_user_id) if snap.updated_by_user_id else None
            ),
            "updated_by_agent": snap.updated_by_agent,
            "created_at": snap.created_at.isoformat(),
            "updated_at": snap.updated_at.isoformat(),
        },
        default=str,
    )


def _decode_overlay_cache(raw: str) -> OverlaySnapshot | None:
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        return OverlaySnapshot(
            brand_id=uuid.UUID(data["brand_id"]),
            workspace_channel_id=uuid.UUID(data["workspace_channel_id"]),
            payload=dict(data.get("payload") or {}),
            version=int(data["version"]),
            updated_by_user_id=(
                uuid.UUID(data["updated_by_user_id"]) if data.get("updated_by_user_id") else None
            ),
            updated_by_agent=data.get("updated_by_agent"),
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
        )
    except (KeyError, ValueError, TypeError):
        return None


async def _cache_get(redis: Any, key: str) -> str | None:
    try:
        value = await redis.get(key)
    except Exception as exc:
        logger.warning(
            "brand_memory.cache.read_failed",
            key=key,
            error=exc.__class__.__name__,
        )
        return None
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


async def _cache_set(redis: Any, key: str, raw: str) -> None:
    try:
        await redis.set(key, raw, ex=BRAND_MEMORY_CACHE_TTL_SECONDS)
    except Exception as exc:
        logger.warning(
            "brand_memory.cache.write_failed",
            key=key,
            error=exc.__class__.__name__,
        )


async def _cache_delete(redis: Any, key: str) -> None:
    try:
        await redis.delete(key)
    except Exception as exc:
        logger.warning(
            "brand_memory.cache.invalidate_failed",
            key=key,
            error=exc.__class__.__name__,
        )


# ---------------------------------------------------------------------------
# Public surface — core
# ---------------------------------------------------------------------------


async def get_core(
    session: AsyncSession,
    redis: Any,
    *,
    brand_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> CoreSnapshot:
    """Return the brand's core memory, materialising an empty row on first read.

    The first call for a brand creates an empty ``brand_memory_core``
    row so subsequent PATCH calls have a row to bump (idempotent
    "lazy init" pattern matching the rest of the codebase — same
    move as ``workspaces.ensure_default`` in PR #14).
    """

    cache_key = _core_cache_key(brand_id)
    cached_raw = await _cache_get(redis, cache_key)
    if cached_raw is not None:
        cached = _decode_core_cache(cached_raw)
        if cached is not None:
            return cached

    row = await _load_core_row(session, brand_id=brand_id)
    if row is None:
        row = BrandMemoryCore(
            workspace_id=workspace_id,
            brand_id=brand_id,
            payload={},
            version=1,
        )
        session.add(row)
        await session.flush()
        # First-read materialisation commits via the caller's unit of
        # work — same pattern :func:`brands_service.default_for_workspace`
        # uses for the legacy ``is_default`` backfill.
        logger.info(
            "brand_memory.core.materialised",
            brand_id=str(brand_id),
            workspace_id=str(workspace_id),
        )

    snapshot = _core_to_snapshot(row)
    await _cache_set(redis, cache_key, _encode_core_cache(snapshot))
    return snapshot


async def update_core(
    session: AsyncSession,
    redis: Any,
    *,
    brand_id: uuid.UUID,
    workspace_id: uuid.UUID,
    payload: dict[str, Any],
    if_match_version: int | None,
    updated_by_user_id: uuid.UUID | None,
    updated_by_agent: str | None,
) -> CoreSnapshot:
    """Apply a version-checked PATCH to ``brand_memory_core``.

    The new ``payload`` replaces the old one wholesale — partial
    deep-merge is intentionally deferred to Sprint 4 (the SPA editor
    in PR #25 will need it). Returns the post-PATCH snapshot.
    """

    if not isinstance(payload, dict):
        # Defensive: the Pydantic schema enforces this already, but
        # the service layer is also called by the OnboardingAgent
        # bypass path in PR #22.
        raise BrandMemoryInvalidPayloadError()

    row = await _load_core_row(session, brand_id=brand_id)
    if row is None:
        # Materialise + immediately update so the caller can PATCH
        # without a prior GET round-trip.
        row = BrandMemoryCore(
            workspace_id=workspace_id,
            brand_id=brand_id,
            payload={},
            version=1,
        )
        session.add(row)
        await session.flush()

    if if_match_version is not None and if_match_version != row.version:
        logger.info(
            "brand_memory.core.version_conflict",
            brand_id=str(brand_id),
            expected=int(if_match_version),
            actual=int(row.version),
        )
        raise BrandMemoryVersionConflictError(
            suggested_action="refresh_and_retry",
            details={
                "expected_version": int(if_match_version),
                "actual_version": int(row.version),
            },
        )

    row.payload = payload
    row.version = int(row.version) + 1
    row.updated_by_user_id = updated_by_user_id
    row.updated_by_agent = updated_by_agent
    # ``onupdate`` on the column doesn't fire for in-Python mutation;
    # set explicitly so the snapshot reflects the write.
    row.updated_at = datetime.now(tz=UTC)
    await session.flush()

    snapshot = _core_to_snapshot(row)
    await _cache_delete(redis, _core_cache_key(brand_id))
    logger.info(
        "brand_memory.core.updated",
        brand_id=str(brand_id),
        workspace_id=str(workspace_id),
        version=snapshot.version,
        updated_by_agent=updated_by_agent,
        updated_by_user_id=(str(updated_by_user_id) if updated_by_user_id else None),
    )
    return snapshot


# ---------------------------------------------------------------------------
# Public surface — overlay
# ---------------------------------------------------------------------------


async def _ensure_channel_bound(
    session: AsyncSession,
    *,
    brand_id: uuid.UUID,
    workspace_channel_id: uuid.UUID,
) -> WorkspaceChannel:
    """Return the active :class:`WorkspaceChannel` for ``brand_id``.

    Raises :class:`BrandMemoryChannelNotBoundError` when:

    * the binding doesn't exist;
    * the binding belongs to a different brand (a malicious caller
      can't enumerate sibling brands' bindings by id-fuzzing);
    * the binding was soft-detached (``disconnected_at IS NOT NULL``).
    """

    res = await session.execute(
        select(WorkspaceChannel).where(
            WorkspaceChannel.id == workspace_channel_id,
        ),
    )
    binding = res.scalar_one_or_none()
    if binding is None or binding.brand_id != brand_id:
        raise BrandMemoryChannelNotBoundError()
    if binding.disconnected_at is not None:
        raise BrandMemoryChannelNotBoundError()
    return binding


async def get_overlay(
    session: AsyncSession,
    redis: Any,
    *,
    brand_id: uuid.UUID,
    workspace_channel_id: uuid.UUID,
) -> OverlaySnapshot | None:
    """Return the brand's overlay for ``workspace_channel_id`` or ``None``.

    The binding is validated first — a cross-brand / detached
    binding short-circuits as
    :class:`BrandMemoryChannelNotBoundError` so the caller surfaces
    a 404 regardless of whether the overlay row itself exists.
    """

    await _ensure_channel_bound(
        session,
        brand_id=brand_id,
        workspace_channel_id=workspace_channel_id,
    )

    cache_key = _overlay_cache_key(brand_id, workspace_channel_id)
    cached_raw = await _cache_get(redis, cache_key)
    if cached_raw is not None:
        cached = _decode_overlay_cache(cached_raw)
        if cached is not None:
            return cached

    row = await _load_overlay_row(
        session,
        brand_id=brand_id,
        workspace_channel_id=workspace_channel_id,
    )
    if row is None:
        return None

    snapshot = _overlay_to_snapshot(row)
    await _cache_set(redis, cache_key, _encode_overlay_cache(snapshot))
    return snapshot


async def update_overlay(
    session: AsyncSession,
    redis: Any,
    *,
    brand_id: uuid.UUID,
    workspace_id: uuid.UUID,
    workspace_channel_id: uuid.UUID,
    payload: dict[str, Any],
    if_match_version: int | None,
    updated_by_user_id: uuid.UUID | None,
    updated_by_agent: str | None,
) -> OverlaySnapshot:
    """Upsert the per-channel overlay; version-checked when one exists."""

    if not isinstance(payload, dict):
        raise BrandMemoryInvalidPayloadError()

    await _ensure_channel_bound(
        session,
        brand_id=brand_id,
        workspace_channel_id=workspace_channel_id,
    )

    row = await _load_overlay_row(
        session,
        brand_id=brand_id,
        workspace_channel_id=workspace_channel_id,
    )

    if row is None:
        # First-time write — ``if_match_version`` is ignored on
        # purpose. The SPA either passes ``None`` (post-GET PATCH on
        # an empty overlay) or passes the version of the empty
        # placeholder it expected; either way conflict semantics
        # only matter once a row exists.
        row = BrandMemoryOverlay(
            workspace_id=workspace_id,
            brand_id=brand_id,
            workspace_channel_id=workspace_channel_id,
            payload=payload,
            version=1,
            updated_by_user_id=updated_by_user_id,
            updated_by_agent=updated_by_agent,
        )
        session.add(row)
        await session.flush()
        snapshot = _overlay_to_snapshot(row)
        await _cache_delete(
            redis,
            _overlay_cache_key(brand_id, workspace_channel_id),
        )
        logger.info(
            "brand_memory.overlay.created",
            brand_id=str(brand_id),
            workspace_id=str(workspace_id),
            workspace_channel_id=str(workspace_channel_id),
            version=snapshot.version,
        )
        return snapshot

    if if_match_version is not None and if_match_version != row.version:
        logger.info(
            "brand_memory.overlay.version_conflict",
            brand_id=str(brand_id),
            workspace_channel_id=str(workspace_channel_id),
            expected=int(if_match_version),
            actual=int(row.version),
        )
        raise BrandMemoryVersionConflictError(
            suggested_action="refresh_and_retry",
            details={
                "expected_version": int(if_match_version),
                "actual_version": int(row.version),
            },
        )

    row.payload = payload
    row.version = int(row.version) + 1
    row.updated_by_user_id = updated_by_user_id
    row.updated_by_agent = updated_by_agent
    row.updated_at = datetime.now(tz=UTC)
    await session.flush()

    snapshot = _overlay_to_snapshot(row)
    await _cache_delete(
        redis,
        _overlay_cache_key(brand_id, workspace_channel_id),
    )
    logger.info(
        "brand_memory.overlay.updated",
        brand_id=str(brand_id),
        workspace_id=str(workspace_id),
        workspace_channel_id=str(workspace_channel_id),
        version=snapshot.version,
        updated_by_agent=updated_by_agent,
    )
    return snapshot


# ---------------------------------------------------------------------------
# Public surface — effective merge
# ---------------------------------------------------------------------------


def _merge_payloads(
    core_payload: dict[str, Any],
    overlay_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    """Shallow-merge ``core`` ⨯ ``overlay``: overlay keys win.

    Sprint 3 keeps the merge shallow — Sprint 4 introduces a deep
    merge (``list[+]``, ``dict[update]``) once the SPA's overlay
    editor is wired and the merge semantics are demonstrably useful.
    """

    merged: dict[str, Any] = dict(core_payload or {})
    if overlay_payload:
        for key, value in overlay_payload.items():
            merged[key] = value
    return merged


async def get_effective(
    session: AsyncSession,
    redis: Any,
    *,
    brand_id: uuid.UUID,
    workspace_id: uuid.UUID,
    workspace_channel_id: uuid.UUID | None,
) -> EffectiveMemory:
    """Return the effective Brand Memory payload for the (brand, channel).

    Always invokes :func:`get_core` (which materialises an empty row
    on first read so the Content Agent has a stable target version
    to reason about). When ``workspace_channel_id`` is supplied, the
    matching overlay (if any) is merged on top of the core payload;
    otherwise the core payload is returned unchanged.
    """

    core = await get_core(
        session,
        redis,
        brand_id=brand_id,
        workspace_id=workspace_id,
    )

    overlay: OverlaySnapshot | None = None
    if workspace_channel_id is not None:
        overlay = await get_overlay(
            session,
            redis,
            brand_id=brand_id,
            workspace_channel_id=workspace_channel_id,
        )

    merged = _merge_payloads(
        core.payload,
        overlay.payload if overlay is not None else None,
    )
    return EffectiveMemory(
        brand_id=brand_id,
        workspace_channel_id=overlay.workspace_channel_id if overlay else None,
        payload=merged,
        core_version=core.version,
        overlay_version=overlay.version if overlay else None,
    )


# ---------------------------------------------------------------------------
# Public surface — examples
# ---------------------------------------------------------------------------


async def list_examples(
    session: AsyncSession,
    *,
    brand_id: uuid.UUID,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[BrandMemoryExample], int]:
    """Return ``(rows, total)`` for the brand's example pool.

    ``limit`` is hard-capped at :data:`LIST_EXAMPLES_MAX_LIMIT` so a
    malicious caller can't ask for a million rows. Ordering is
    ``created_at`` descending so the SPA's "most recent first" list
    works without an extra ``ORDER BY`` parameter.
    """

    if limit < 1:
        limit = 1
    if limit > LIST_EXAMPLES_MAX_LIMIT:
        limit = LIST_EXAMPLES_MAX_LIMIT
    if offset < 0:
        offset = 0

    from sqlalchemy import func as _func

    total_res = await session.execute(
        select(_func.count(BrandMemoryExample.id)).where(
            BrandMemoryExample.brand_id == brand_id,
        ),
    )
    total = int(total_res.scalar_one() or 0)

    rows_res = await session.execute(
        select(BrandMemoryExample)
        .where(BrandMemoryExample.brand_id == brand_id)
        .order_by(BrandMemoryExample.created_at.desc())
        .limit(limit)
        .offset(offset),
    )
    rows = list(rows_res.scalars().all())
    return rows, total


# ---------------------------------------------------------------------------
# Cache invalidation hooks
# ---------------------------------------------------------------------------


async def invalidate_core(redis: Any, brand_id: uuid.UUID | str) -> None:
    """Drop the cached core row for ``brand_id``.

    Mirrors :func:`memberships_cache.invalidate`. Called by the route
    handler on every successful PATCH; the worker side (PR #22's
    OnboardingAgent) calls it after running an extraction pass.
    """

    await _cache_delete(redis, _core_cache_key(brand_id))


async def invalidate_overlay(
    redis: Any,
    brand_id: uuid.UUID | str,
    workspace_channel_id: uuid.UUID | str,
) -> None:
    """Drop the cached overlay row for ``(brand_id, workspace_channel_id)``."""

    await _cache_delete(redis, _overlay_cache_key(brand_id, workspace_channel_id))


__all__ = [
    "BRAND_MEMORY_CACHE_TTL_SECONDS",
    "LIST_EXAMPLES_MAX_LIMIT",
    "CoreSnapshot",
    "EffectiveMemory",
    "OverlaySnapshot",
    "get_core",
    "get_effective",
    "get_overlay",
    "invalidate_core",
    "invalidate_overlay",
    "list_examples",
    "update_core",
    "update_overlay",
]
