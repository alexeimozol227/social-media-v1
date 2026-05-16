"""Integration tests for ``/v1/brands*`` HTTP endpoints (PR #19).

Covers CRUD happy paths, the quota gate, default-flag bookkeeping,
RLS isolation between workspaces, soft-delete semantics, and the
``/quota`` endpoint that powers the SPA settings header.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.plan import Plan
from app.models.tenant_limit_override import TenantLimitOverride
from app.services.billing.seed import PLAN_SEED


@pytest_asyncio.fixture(autouse=True)
async def _seeded_plan_catalog(db_session: AsyncSession) -> None:
    """Seed the plan catalog so :func:`get_active_plan_for_workspace` resolves.

    The /v1/brands/quota endpoint resolves the workspace's effective
    plan via :func:`app.services.billing.plans.get_active_plan_for_workspace`
    which falls back to the seeded ``solo`` plan when no paid invoice
    exists. The conftest doesn't run alembic data-migrations, so we
    materialise the seed inline before every test.
    """

    existing = await db_session.execute(select(Plan.code))
    if existing.scalars().first() is not None:
        return
    for spec in PLAN_SEED:
        db_session.add(
            Plan(
                code=spec["code"],
                tier=spec["tier"],
                name=spec["name"],
                description=spec.get("description"),
                max_brands=spec["max_brands"],
                max_posts_per_month=spec["max_posts_per_month"],
                max_ai_text_per_month=spec["max_ai_text_per_month"],
                max_ai_media_per_month=spec["max_ai_media_per_month"],
                max_channels_per_brand=spec["max_channels_per_brand"],
                max_competitors=spec["max_competitors"],
                features=spec["features"],
                enabled_agents=spec["enabled_agents"],
                active=True,
                sort_order=spec["sort_order"],
            ),
        )
    await db_session.commit()


async def _bump_brand_quota(
    db_session: AsyncSession,
    *,
    workspace_id: str,
    max_brands: int = 3,
) -> None:
    """Insert a TenantLimitOverride row so a workspace can hold >1 brand."""

    db_session.add(
        TenantLimitOverride(
            workspace_id=uuid.UUID(workspace_id),
            max_brands=max_brands,
            max_posts_per_month=999,
            reason="test",
        ),
    )
    await db_session.commit()


async def _login_as(
    client: AsyncClient,
    *,
    email: str,
    password: str = "S3curePass!",
) -> dict[str, Any]:
    """Register (or noop) + login + set Authorization + return /auth/me."""

    await client.post(
        "/v1/auth/register",
        json={"email": email, "password": password, "tos_accepted": True},
    )
    login = await client.post(
        "/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert login.status_code == 200, login.text
    client.headers.update({"Authorization": f"Bearer {login.json()['access_token']}"})
    me = await client.get("/v1/auth/me")
    return me.json()


@pytest_asyncio.fixture
async def authed(
    client: AsyncClient,
) -> AsyncIterator[tuple[AsyncClient, dict[str, Any]]]:
    """Register + login + return ``(client, me)``."""

    me = await _login_as(client, email="brands@example.com")
    yield client, me


# ---------------------------------------------------------------------------
# List + quota baseline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_brands_returns_default_only_for_new_workspace(
    authed: tuple[AsyncClient, dict[str, Any]],
) -> None:
    client, _ = authed
    resp = await client.get("/v1/brands")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) == 1
    assert body[0]["is_default"] is True
    assert body[0]["name"]


@pytest.mark.asyncio
async def test_get_brand_quota_returns_solo_plan_baseline(
    authed: tuple[AsyncClient, dict[str, Any]],
) -> None:
    client, _ = authed
    resp = await client.get("/v1/brands/quota")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["plan_code"] == "solo"
    assert body["max_brands"] == 1
    assert body["used_brands"] == 1
    assert body["max_channels_per_brand"] == 1
    assert body["max_competitors"] == 5
    assert body["override_active"] is False


# ---------------------------------------------------------------------------
# POST /v1/brands
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_brand_quota_exceeded_on_solo_plan(
    authed: tuple[AsyncClient, dict[str, Any]],
) -> None:
    """Solo plan caps at 1 brand; a second create must 402."""

    client, _ = authed
    resp = await client.post(
        "/v1/brands",
        json={"name": "Second"},
    )
    assert resp.status_code == 402, resp.text
    payload = resp.json()
    assert payload["error_code"] == "BRAND_QUOTA_EXCEEDED"
    assert payload["details"]["max_brands"] == 1
    assert payload["details"]["used_brands"] == 1


@pytest.mark.asyncio
async def test_create_brand_succeeds_under_higher_override(
    authed: tuple[AsyncClient, dict[str, Any]],
    db_session: AsyncSession,
) -> None:
    """Bump the override to 3 brands and create the second brand."""

    client, me = authed
    await _bump_brand_quota(
        db_session,
        workspace_id=me["active_workspace"]["id"],
    )

    resp = await client.post(
        "/v1/brands",
        json={
            "name": "Secondary",
            "content_language": "en",
            "timezone": "Europe/Berlin",
            "is_default": False,
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Secondary"
    assert body["content_language"] == "en"
    assert body["timezone"] == "Europe/Berlin"
    assert body["is_default"] is False

    quota = await client.get("/v1/brands/quota")
    assert quota.json()["used_brands"] == 2
    assert quota.json()["max_brands"] == 3
    assert quota.json()["override_active"] is True


@pytest.mark.asyncio
async def test_create_brand_rejects_extra_fields(
    authed: tuple[AsyncClient, dict[str, Any]],
) -> None:
    client, _ = authed
    resp = await client.post(
        "/v1/brands",
        json={"name": "Hacky", "is_admin": True},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# PATCH /v1/brands/{id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_brand_updates_metadata(
    authed: tuple[AsyncClient, dict[str, Any]],
) -> None:
    client, _ = authed
    listing = await client.get("/v1/brands")
    brand_id = listing.json()[0]["id"]

    resp = await client.patch(
        f"/v1/brands/{brand_id}",
        json={"name": "Renamed", "timezone": "Asia/Tashkent"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "Renamed"
    assert body["timezone"] == "Asia/Tashkent"
    # Untouched fields preserved
    assert body["content_language"]


@pytest.mark.asyncio
async def test_patch_unknown_brand_returns_403(
    authed: tuple[AsyncClient, dict[str, Any]],
) -> None:
    """Any brand id that isn't in the caller's workspace returns 403."""

    client, _ = authed
    bogus = "11111111-2222-3333-4444-555555555555"
    resp = await client.patch(
        f"/v1/brands/{bogus}",
        json={"name": "Hijacked"},
    )
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "BRAND_NOT_IN_WORKSPACE"


# ---------------------------------------------------------------------------
# POST /v1/brands/{id}/default + DELETE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_default_swaps_atomically(
    authed: tuple[AsyncClient, dict[str, Any]],
    db_session: AsyncSession,
) -> None:
    client, me = authed
    await _bump_brand_quota(
        db_session,
        workspace_id=me["active_workspace"]["id"],
    )

    second = await client.post("/v1/brands", json={"name": "Second"})
    assert second.status_code == 201
    new_id = second.json()["id"]
    assert second.json()["is_default"] is False

    promote = await client.post(f"/v1/brands/{new_id}/default")
    assert promote.status_code == 200
    assert promote.json()["is_default"] is True

    listing = (await client.get("/v1/brands")).json()
    defaults = [b for b in listing if b["is_default"]]
    assert len(defaults) == 1
    assert defaults[0]["id"] == new_id


@pytest.mark.asyncio
async def test_delete_last_brand_blocked(
    authed: tuple[AsyncClient, dict[str, Any]],
) -> None:
    client, _ = authed
    brand_id = (await client.get("/v1/brands")).json()[0]["id"]
    resp = await client.delete(f"/v1/brands/{brand_id}")
    assert resp.status_code == 409
    assert resp.json()["error_code"] == "BRAND_DELETE_LAST_BLOCKED"


@pytest.mark.asyncio
async def test_delete_default_brand_blocked_when_others_exist(
    authed: tuple[AsyncClient, dict[str, Any]],
    db_session: AsyncSession,
) -> None:
    client, me = authed
    await _bump_brand_quota(
        db_session,
        workspace_id=me["active_workspace"]["id"],
    )

    await client.post("/v1/brands", json={"name": "Second"})
    default_id = (await client.get("/v1/brands")).json()[0]["id"]
    # Make sure that's the default one
    default_brand = next(b for b in (await client.get("/v1/brands")).json() if b["is_default"])
    resp = await client.delete(f"/v1/brands/{default_brand['id']}")
    assert resp.status_code == 409
    assert resp.json()["error_code"] == "BRAND_DELETE_DEFAULT_BLOCKED"
    # untouched
    assert default_id


@pytest.mark.asyncio
async def test_delete_non_default_brand_succeeds(
    authed: tuple[AsyncClient, dict[str, Any]],
    db_session: AsyncSession,
) -> None:
    client, me = authed
    await _bump_brand_quota(
        db_session,
        workspace_id=me["active_workspace"]["id"],
    )

    created = await client.post("/v1/brands", json={"name": "Disposable"})
    brand_id = created.json()["id"]
    resp = await client.delete(f"/v1/brands/{brand_id}")
    assert resp.status_code == 204
    # No longer listed
    remaining = (await client.get("/v1/brands")).json()
    assert all(b["id"] != brand_id for b in remaining)
    # Quota usage decremented
    quota = (await client.get("/v1/brands/quota")).json()
    assert quota["used_brands"] == 1


# ---------------------------------------------------------------------------
# GET /v1/brands/{id}/dashboard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dashboard_no_active_channel_for_fresh_brand(
    authed: tuple[AsyncClient, dict[str, Any]],
) -> None:
    client, _ = authed
    brand_id = (await client.get("/v1/brands")).json()[0]["id"]
    resp = await client.get(f"/v1/brands/{brand_id}/dashboard")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "no_active_channel"
    assert body["channel"] is None
    assert body["recent_posts"] == []


@pytest.mark.asyncio
async def test_dashboard_unknown_brand_returns_403(
    authed: tuple[AsyncClient, dict[str, Any]],
) -> None:
    client, _ = authed
    bogus = "11111111-2222-3333-4444-555555555555"
    resp = await client.get(f"/v1/brands/{bogus}/dashboard")
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "BRAND_NOT_IN_WORKSPACE"
