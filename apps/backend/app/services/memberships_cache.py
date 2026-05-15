"""Redis-backed memberships cache (D64 in docs/04 §18.6).

Keep the JWT to the strict minimum claim set (``sub`` / ``platform_role``
/ ``active_workspace_id`` / ``exp`` / ``iat`` / ``jti`` / ``tv`` / ``type``)
and look up the user's workspace memberships on every authenticated
request out of Redis instead of the database.

Rationale (docs/04-architecture.md §18.6 + docs/05-tech-stack.md §6.6):

* Agencies will have dozens — eventually hundreds — of workspaces per
  user. Embedding the full membership list inside the access token
  would blow it past the 2 KB invariant the CI test in PR #14 will
  pin, and would also leak revoked roles for the rest of the access
  window (15 min).
* The Redis cache TTL (5 min) bounds how stale a membership entry can
  be without an explicit invalidation. The role-mutation path on the
  admin / settings side calls :func:`invalidate` immediately and also
  publishes :class:`AuthRefreshRequiredEvent` on the user's per-user
  WS channel so the SPA dispatches a one-shot ``POST /v1/auth/refresh``
  — the next access token reflects the change without the user having
  to sign out.

Schema of one cached entry::

    {
        "workspace_id": "<uuid>",
        "role":         "owner|admin|editor|reviewer|viewer|analyst",
        "brand_ids":    ["<uuid>", ...] | null,    # null = full access
    }

Two failure modes are explicit:

* **Cache miss / Redis blip** — fall back to a single ``SELECT`` from
  ``workspace_members`` and re-prime the key. Always returns the
  authoritative answer; the cache is a latency optimisation, not the
  source of truth.
* **Empty list** — the cache stores ``[]`` so the next read still
  short-circuits the DB (negative-cache; D64 explicitly calls this
  out — a user without any workspaces should not pay a round-trip on
  every request).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.workspace_member import WorkspaceMember

logger = get_logger(__name__)

# 5-minute TTL matches docs/04 §18.6 and docs/05 §6.6. Bumping this
# stretches the window during which a revoked role is still
# resolvable by a logged-in session — every increment must be
# weighed against the WS-push fallback latency.
MEMBERSHIP_CACHE_TTL_SECONDS = 300

_KEY_PREFIX = "user"
_KEY_SUFFIX = "memberships"


def cache_key(user_id: uuid.UUID | str) -> str:
    """Return the Redis key for ``user_id``'s memberships."""

    return f"{_KEY_PREFIX}:{user_id}:{_KEY_SUFFIX}"


def _serialize_brand_ids(value: Any) -> list[str] | None:
    """Coerce SQLAlchemy's ``ARRAY``-or-``JSON`` column into a JSON-safe list.

    The model uses ``ARRAY(UUID)`` on Postgres and a ``JSON`` fallback
    on SQLite, so we normalise both representations here.
    """

    if value is None:
        return None
    if isinstance(value, list):
        return [str(v) for v in value]
    # ``None`` / scalar / unexpected — surface as no restriction.
    return None


async def _load_from_db(
    session: AsyncSession,
    user_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """Return every workspace_members row for ``user_id`` as a JSON-safe list."""

    res = await session.execute(
        select(WorkspaceMember).where(WorkspaceMember.user_id == user_id),
    )
    rows = list(res.scalars().all())
    return [
        {
            "workspace_id": str(row.workspace_id),
            "role": row.role,
            "brand_ids": _serialize_brand_ids(row.brand_ids),
        }
        for row in rows
    ]


async def get_memberships(
    redis: Any,
    session: AsyncSession,
    user_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """Return cached memberships, repopulating from the DB on miss.

    Both arguments are required because the cache is a latency
    optimisation, not a source of truth — a Redis blip must still
    serve the request from the DB transparently.

    Returns a list of ``{workspace_id, role, brand_ids}`` dicts. An
    empty list is a valid (cached) answer for a user who hasn't
    joined any workspace yet.
    """

    key = cache_key(user_id)
    cached_raw: str | None = None
    try:
        cached_raw = await redis.get(key)
    except Exception as exc:
        logger.warning(
            "memberships.cache.read_failed",
            user_id=str(user_id),
            error=exc.__class__.__name__,
        )

    if cached_raw is not None:
        try:
            parsed = json.loads(cached_raw)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, list):
            return [m for m in parsed if isinstance(m, dict)]

    memberships = await _load_from_db(session, user_id)
    try:
        await redis.set(
            key,
            json.dumps(memberships),
            ex=MEMBERSHIP_CACHE_TTL_SECONDS,
        )
    except Exception as exc:
        # Cache writes are best-effort. Already-fetched DB list still
        # serves the current request.
        logger.warning(
            "memberships.cache.write_failed",
            user_id=str(user_id),
            error=exc.__class__.__name__,
        )
    return memberships


async def invalidate(redis: Any, user_id: uuid.UUID | str) -> None:
    """Drop ``user_id``'s membership cache entry.

    Called by the role-mutation path (admin invites / removes a
    member, edits a role, etc.) so the next request rebuilds the
    cache from a fresh DB read.

    Pair this with :class:`app.events.schemas.AuthRefreshRequiredEvent`
    over the user's per-user WS channel — the SPA reacts by issuing
    a one-shot ``POST /v1/auth/refresh`` so the new access token
    reflects the change without waiting for the previous one to
    expire.
    """

    key = cache_key(user_id)
    try:
        await redis.delete(key)
    except Exception as exc:
        # Stale cache will expire on its own at the 5-min TTL. The WS
        # push still triggers the refresh, so a Redis blip on
        # invalidate is bounded.
        logger.warning(
            "memberships.cache.invalidate_failed",
            user_id=str(user_id),
            error=exc.__class__.__name__,
        )


__all__ = [
    "MEMBERSHIP_CACHE_TTL_SECONDS",
    "cache_key",
    "get_memberships",
    "invalidate",
]
