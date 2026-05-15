"""Resolve a workspace's effective quotas (docs/04 §16.4, docs/06 §11.4).

The plan baseline lives on :class:`app.models.plan.Plan` (``max_brands``,
``max_posts_per_month``, ``max_tokens_per_month`` (TODO), …). VIP /
promo overrides live on :class:`app.models.tenant_limit_override.TenantLimitOverride`
keyed by workspace.

:func:`resolve_for_workspace` is the single read-through helper the
quota middleware will call: it merges the plan baseline with the
most-recent active override row for the workspace and returns a
typed :class:`EffectiveLimits` snapshot.

Resolution rules (D-quotas in docs/04 §16.4):

* NULL in any override column → inherit the plan baseline for that column.
* ``valid_until`` NULL or in the future → override row is "active".
* If multiple active rows exist for the same workspace, the most
  recently created row wins (last-write-wins; the admin UI is
  expected to expire the previous row when issuing a new one,
  but we keep the resolver defensive against historical rows
  that linger past their effective window).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.plan import Plan
from app.models.tenant_limit_override import TenantLimitOverride


@dataclass(frozen=True)
class EffectiveLimits:
    """Resolved per-workspace quota ceiling.

    Each field is the value the quota middleware will compare a
    new write against. The dataclass is frozen so the snapshot is
    safe to hand around request-state.

    ``override_id`` is set when at least one column came from a
    :class:`TenantLimitOverride` row — the admin UI uses it to
    surface "this workspace has an active VIP override" badge
    next to the limit.
    """

    plan_id: uuid.UUID
    max_brands: int
    max_posts_per_month: int
    max_tokens_per_month: int | None
    max_usd_per_month: Decimal | None
    override_id: uuid.UUID | None


async def _active_override(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    at: datetime | None = None,
) -> TenantLimitOverride | None:
    """Return the most recently created active override for ``workspace_id``.

    ``at`` defaults to UTC now — the parameter is exposed so tests
    can pin a clock without resorting to time-machine fixtures.
    """

    now = at or datetime.now(tz=UTC)
    res = await session.execute(
        select(TenantLimitOverride)
        .where(
            and_(
                TenantLimitOverride.workspace_id == workspace_id,
                or_(
                    TenantLimitOverride.valid_until.is_(None),
                    TenantLimitOverride.valid_until > now,
                ),
            ),
        )
        .order_by(TenantLimitOverride.created_at.desc())
        .limit(1),
    )
    return res.scalar_one_or_none()


async def resolve_for_workspace(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    plan: Plan,
    at: datetime | None = None,
) -> EffectiveLimits:
    """Return ``workspace_id``'s effective quota ceiling.

    The plan must be passed in by the caller (the billing module
    already loads it for the same request — passing it through
    avoids a second SELECT).

    ``max_tokens_per_month`` / ``max_usd_per_month`` aren't on the
    :class:`Plan` baseline today; the plan baseline returns
    ``None`` for them so the override is the only source. Once
    those plan columns land in a follow-up migration, this
    resolver picks them up without code changes.
    """

    override = await _active_override(session, workspace_id, at=at)

    if override is None:
        return EffectiveLimits(
            plan_id=plan.id,
            max_brands=plan.max_brands,
            max_posts_per_month=plan.max_posts_per_month,
            max_tokens_per_month=None,
            max_usd_per_month=None,
            override_id=None,
        )

    return EffectiveLimits(
        plan_id=plan.id,
        max_brands=(override.max_brands if override.max_brands is not None else plan.max_brands),
        max_posts_per_month=(
            override.max_posts_per_month
            if override.max_posts_per_month is not None
            else plan.max_posts_per_month
        ),
        max_tokens_per_month=override.max_tokens_per_month,
        max_usd_per_month=override.max_usd_per_month,
        override_id=override.id,
    )


__all__ = ["EffectiveLimits", "resolve_for_workspace"]
