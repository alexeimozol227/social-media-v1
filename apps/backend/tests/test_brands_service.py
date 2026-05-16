"""Unit tests for :mod:`app.services.brands` write helpers (PR #19).

The service is exercised against the SQLite test schema. Production
Postgres parity is covered by the ``backend-postgres`` CI job which
re-runs the same suite against the real DB so the partial unique
index ``ux_brands_workspace_default`` is exercised end-to-end.

Naming convention mirrors :mod:`tests.test_channels_service`.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import (
    BrandDeleteDefaultBlockedError,
    BrandDeleteLastBlockedError,
    BrandNameRequiredError,
    BrandQuotaExceededError,
)
from app.models.brand import Brand
from app.models.user import User, UserStatus
from app.models.workspace import Workspace, WorkspaceType
from app.services import brands as brands_service


async def _seed_workspace(
    session: AsyncSession,
    *,
    initial_brand_name: str | None = "Default Brand",
) -> tuple[User, Workspace, Brand | None]:
    """Create one user + workspace and (optionally) one default brand."""

    user = User(
        email=f"svc-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
        full_name="Svc",
        locale="ru-RU",
        timezone="UTC",
        preferred_currency="RUB",
        status=UserStatus.ACTIVE,
    )
    session.add(user)
    await session.flush()
    workspace = Workspace(
        owner_id=user.id,
        name="WS",
        slug=f"ws-{uuid.uuid4().hex[:8]}",
        type=WorkspaceType.SOLO,
        preferred_currency="RUB",
    )
    session.add(workspace)
    await session.flush()
    brand: Brand | None = None
    if initial_brand_name is not None:
        brand = Brand(
            workspace_id=workspace.id,
            name=initial_brand_name,
            content_language="ru",
            timezone="Europe/Minsk",
            is_default=True,
        )
        session.add(brand)
        await session.flush()
    return user, workspace, brand


# ---------------------------------------------------------------------------
# count_for_workspace
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_count_for_workspace_excludes_soft_deleted(
    db_session: AsyncSession,
) -> None:
    _, workspace, default = await _seed_workspace(db_session)
    assert default is not None
    extra = Brand(
        workspace_id=workspace.id,
        name="Extra",
        content_language="ru",
        timezone="Europe/Minsk",
        is_default=False,
    )
    db_session.add(extra)
    await db_session.flush()

    assert await brands_service.count_for_workspace(db_session, workspace.id) == 2

    extra.deleted_at = default.created_at  # any non-null timestamp
    await db_session.flush()

    assert await brands_service.count_for_workspace(db_session, workspace.id) == 1


# ---------------------------------------------------------------------------
# create_brand
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_brand_first_brand_is_forced_default(
    db_session: AsyncSession,
) -> None:
    """An empty workspace's first brand is always promoted to default."""

    _, workspace, _ = await _seed_workspace(db_session, initial_brand_name=None)
    brand = await brands_service.create_brand(
        db_session,
        workspace_id=workspace.id,
        name="First",
        is_default=False,
        max_brands=3,
    )
    assert brand.is_default is True
    assert brand.name == "First"


@pytest.mark.asyncio
async def test_create_brand_default_demotes_previous_default(
    db_session: AsyncSession,
) -> None:
    _, workspace, original = await _seed_workspace(db_session)
    assert original is not None
    assert original.is_default is True

    new_brand = await brands_service.create_brand(
        db_session,
        workspace_id=workspace.id,
        name="New Default",
        is_default=True,
        max_brands=3,
    )
    await db_session.refresh(original)
    assert new_brand.is_default is True
    assert original.is_default is False


@pytest.mark.asyncio
async def test_create_brand_non_default_keeps_previous_default(
    db_session: AsyncSession,
) -> None:
    _, workspace, original = await _seed_workspace(db_session)
    assert original is not None

    secondary = await brands_service.create_brand(
        db_session,
        workspace_id=workspace.id,
        name="Secondary",
        is_default=False,
        max_brands=3,
    )
    await db_session.refresh(original)
    assert original.is_default is True
    assert secondary.is_default is False


@pytest.mark.asyncio
async def test_create_brand_rejects_blank_name(
    db_session: AsyncSession,
) -> None:
    _, workspace, _ = await _seed_workspace(db_session)
    with pytest.raises(BrandNameRequiredError):
        await brands_service.create_brand(
            db_session,
            workspace_id=workspace.id,
            name="   ",
            max_brands=3,
        )


@pytest.mark.asyncio
async def test_create_brand_quota_exceeded_raises(
    db_session: AsyncSession,
) -> None:
    _, workspace, _ = await _seed_workspace(db_session)

    with pytest.raises(BrandQuotaExceededError) as exc:
        await brands_service.create_brand(
            db_session,
            workspace_id=workspace.id,
            name="Over Quota",
            max_brands=1,
        )
    assert exc.value.details["used_brands"] == 1
    assert exc.value.details["max_brands"] == 1
    assert exc.value.suggested_action == "upgrade_plan"


@pytest.mark.asyncio
async def test_create_brand_quota_ignores_soft_deleted(
    db_session: AsyncSession,
) -> None:
    """Soft-deleted brands don't count against the quota."""

    _, workspace, default = await _seed_workspace(db_session)
    assert default is not None
    # Soft-delete the default so we have room for a new one even at max=1.
    # First make a temp brand to allow the delete (last-brand guard).
    temp = await brands_service.create_brand(
        db_session,
        workspace_id=workspace.id,
        name="Temp",
        max_brands=5,
    )
    await brands_service.set_default(db_session, workspace_id=workspace.id, brand=temp)
    await db_session.refresh(default)
    await brands_service.delete_brand(
        db_session,
        workspace_id=workspace.id,
        brand=default,
    )

    new_brand = await brands_service.create_brand(
        db_session,
        workspace_id=workspace.id,
        name="Fresh",
        max_brands=2,
    )
    assert new_brand.is_default is False
    # Count returns 2 (temp + Fresh); soft-deleted "Default Brand" excluded.
    assert await brands_service.count_for_workspace(db_session, workspace.id) == 2


# ---------------------------------------------------------------------------
# update_brand
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_brand_changes_only_provided_fields(
    db_session: AsyncSession,
) -> None:
    _, _, brand = await _seed_workspace(db_session)
    assert brand is not None
    updated, changed = await brands_service.update_brand(
        db_session,
        brand=brand,
        name="Renamed",
    )
    assert updated.name == "Renamed"
    assert changed == ["name"]


@pytest.mark.asyncio
async def test_update_brand_no_op_when_values_unchanged(
    db_session: AsyncSession,
) -> None:
    _, _, brand = await _seed_workspace(db_session)
    assert brand is not None
    _, changed = await brands_service.update_brand(
        db_session,
        brand=brand,
        name=brand.name,
        content_language=brand.content_language,
        timezone=brand.timezone,
    )
    assert changed == []


@pytest.mark.asyncio
async def test_update_brand_rejects_blank_name(
    db_session: AsyncSession,
) -> None:
    _, _, brand = await _seed_workspace(db_session)
    assert brand is not None
    with pytest.raises(BrandNameRequiredError):
        await brands_service.update_brand(
            db_session,
            brand=brand,
            name="   ",
        )


# ---------------------------------------------------------------------------
# set_default
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_default_swaps_flag_atomically(
    db_session: AsyncSession,
) -> None:
    _, workspace, original = await _seed_workspace(db_session)
    assert original is not None
    secondary = await brands_service.create_brand(
        db_session,
        workspace_id=workspace.id,
        name="Secondary",
        max_brands=3,
    )

    promoted = await brands_service.set_default(
        db_session,
        workspace_id=workspace.id,
        brand=secondary,
    )
    await db_session.refresh(original)
    assert promoted.is_default is True
    assert original.is_default is False

    # Verify partial-unique invariant holds — exactly one is_default=True.
    res = await db_session.execute(
        select(Brand).where(
            Brand.workspace_id == workspace.id,
            Brand.is_default.is_(True),
            Brand.deleted_at.is_(None),
        ),
    )
    defaults = list(res.scalars().all())
    assert len(defaults) == 1
    assert defaults[0].id == secondary.id


@pytest.mark.asyncio
async def test_set_default_idempotent_on_current_default(
    db_session: AsyncSession,
) -> None:
    _, workspace, original = await _seed_workspace(db_session)
    assert original is not None

    promoted = await brands_service.set_default(
        db_session,
        workspace_id=workspace.id,
        brand=original,
    )
    assert promoted.is_default is True
    assert promoted.id == original.id


# ---------------------------------------------------------------------------
# delete_brand
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_brand_blocks_last_brand(
    db_session: AsyncSession,
) -> None:
    _, workspace, brand = await _seed_workspace(db_session)
    assert brand is not None
    with pytest.raises(BrandDeleteLastBlockedError):
        await brands_service.delete_brand(
            db_session,
            workspace_id=workspace.id,
            brand=brand,
        )


@pytest.mark.asyncio
async def test_delete_brand_blocks_default_when_others_exist(
    db_session: AsyncSession,
) -> None:
    _, workspace, default = await _seed_workspace(db_session)
    assert default is not None
    await brands_service.create_brand(
        db_session,
        workspace_id=workspace.id,
        name="Secondary",
        max_brands=3,
    )

    with pytest.raises(BrandDeleteDefaultBlockedError):
        await brands_service.delete_brand(
            db_session,
            workspace_id=workspace.id,
            brand=default,
        )


@pytest.mark.asyncio
async def test_delete_brand_soft_deletes_non_default(
    db_session: AsyncSession,
) -> None:
    _, workspace, _ = await _seed_workspace(db_session)
    secondary = await brands_service.create_brand(
        db_session,
        workspace_id=workspace.id,
        name="Secondary",
        max_brands=3,
    )

    deleted = await brands_service.delete_brand(
        db_session,
        workspace_id=workspace.id,
        brand=secondary,
    )
    assert deleted.deleted_at is not None
    assert await brands_service.count_for_workspace(db_session, workspace.id) == 1
