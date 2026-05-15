"""Tenant limit overrides + quota resolver (PR #12).

docs/04 §10.6 + §16.4 + docs/06 §5 Спринт 1 + docs/06 §11.4.

The resolver merges the plan baseline with the most-recent active
``tenant_limit_overrides`` row for the workspace. These tests pin
the resolution rules end-to-end on SQLite (which is enough — the
RLS policy on the table is exercised separately in the Postgres
integration suite).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Plan, TenantLimitOverride, Workspace
from app.models.workspace import WorkspaceType
from app.services.billing.quotas import resolve_for_workspace


def _make_plan(**kwargs: object) -> Plan:
    defaults: dict[str, object] = {
        "code": "test-tier",
        "tier": "pro",
        "name": "Test Pro",
        "max_brands": 3,
        "max_posts_per_month": 100,
    }
    defaults.update(kwargs)
    return Plan(**defaults)


async def _seed_workspace_and_plan(
    db_session: AsyncSession,
    **plan_kwargs: object,
) -> tuple[Workspace, Plan]:
    plan = _make_plan(**plan_kwargs)
    db_session.add(plan)
    await db_session.flush()
    workspace = Workspace(
        owner_id=uuid.uuid4(),  # FK is enforced by Postgres only.
        name="WS",
        slug=f"ws-{uuid.uuid4().hex[:8]}",
        type=WorkspaceType.SOLO,
        preferred_currency="RUB",
    )
    db_session.add(workspace)
    await db_session.flush()
    return workspace, plan


@pytest.mark.asyncio
async def test_resolve_returns_plan_baseline_when_no_override(
    db_session: AsyncSession,
) -> None:
    workspace, plan = await _seed_workspace_and_plan(db_session)

    out = await resolve_for_workspace(
        db_session,
        workspace_id=workspace.id,
        plan=plan,
    )

    assert out.plan_id == plan.id
    assert out.max_brands == plan.max_brands
    assert out.max_posts_per_month == plan.max_posts_per_month
    assert out.max_tokens_per_month is None
    assert out.max_usd_per_month is None
    assert out.override_id is None


@pytest.mark.asyncio
async def test_resolve_applies_override_for_set_columns_only(
    db_session: AsyncSession,
) -> None:
    """Override bumps ``max_brands`` only — ``max_posts_per_month``
    falls through to the plan baseline."""

    workspace, plan = await _seed_workspace_and_plan(
        db_session,
        max_brands=3,
        max_posts_per_month=100,
    )
    override = TenantLimitOverride(
        workspace_id=workspace.id,
        max_brands=25,
        # max_posts_per_month left NULL.
        max_usd_per_month=Decimal("500.00"),
        reason="VIP pilot",
    )
    db_session.add(override)
    await db_session.flush()

    out = await resolve_for_workspace(
        db_session,
        workspace_id=workspace.id,
        plan=plan,
    )

    assert out.max_brands == 25  # from override
    assert out.max_posts_per_month == 100  # from plan
    assert out.max_usd_per_month == Decimal("500.00")
    assert out.override_id == override.id


@pytest.mark.asyncio
async def test_resolve_ignores_expired_override(
    db_session: AsyncSession,
) -> None:
    """``valid_until`` in the past → override is ignored."""

    workspace, plan = await _seed_workspace_and_plan(db_session)
    past = datetime.now(tz=UTC) - timedelta(days=1)
    db_session.add(
        TenantLimitOverride(
            workspace_id=workspace.id,
            max_brands=99,
            valid_until=past,
            reason="expired pilot",
        ),
    )
    await db_session.flush()

    out = await resolve_for_workspace(
        db_session,
        workspace_id=workspace.id,
        plan=plan,
    )

    assert out.max_brands == plan.max_brands
    assert out.override_id is None


@pytest.mark.asyncio
async def test_resolve_picks_most_recent_active_override(
    db_session: AsyncSession,
) -> None:
    """Two active rows for the same workspace → the most recent wins.

    SQLite resolves ``func.now()`` once per transaction so we set
    ``created_at`` by hand to disambiguate; Postgres ``clock_timestamp``
    would already separate them in production but the contract under
    test is the resolver's ``ORDER BY created_at DESC``.
    """

    workspace, plan = await _seed_workspace_and_plan(
        db_session,
        max_brands=3,
    )
    now = datetime.now(tz=UTC)
    older = TenantLimitOverride(
        workspace_id=workspace.id,
        max_brands=10,
        reason="initial pilot",
        created_at=now - timedelta(days=1),
        updated_at=now - timedelta(days=1),
    )
    db_session.add(older)
    await db_session.flush()
    newer = TenantLimitOverride(
        workspace_id=workspace.id,
        max_brands=50,
        reason="upgrade",
        created_at=now,
        updated_at=now,
    )
    db_session.add(newer)
    await db_session.flush()

    out = await resolve_for_workspace(
        db_session,
        workspace_id=workspace.id,
        plan=plan,
    )

    assert out.max_brands == 50
    assert out.override_id == newer.id


@pytest.mark.asyncio
async def test_resolve_at_clock_pin_excludes_future_active_override(
    db_session: AsyncSession,
) -> None:
    """Pinning ``at`` past ``valid_until`` reproduces an expired window."""

    workspace, plan = await _seed_workspace_and_plan(db_session)
    one_hour_from_now = datetime.now(tz=UTC) + timedelta(hours=1)
    db_session.add(
        TenantLimitOverride(
            workspace_id=workspace.id,
            max_brands=42,
            valid_until=one_hour_from_now,
            reason="time-boxed pilot",
        ),
    )
    await db_session.flush()

    # 2 hours from now — past the override's valid_until.
    two_hours_from_now = datetime.now(tz=UTC) + timedelta(hours=2)
    out = await resolve_for_workspace(
        db_session,
        workspace_id=workspace.id,
        plan=plan,
        at=two_hours_from_now,
    )

    assert out.max_brands == plan.max_brands
    assert out.override_id is None
