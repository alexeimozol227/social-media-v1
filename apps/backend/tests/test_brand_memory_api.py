"""Integration tests for ``/v1/brands/{id}/memory*`` endpoints (PR #21).

Cover the HTTP surface — auth, tenant gate, version-checked PATCH,
overlay 404, examples list, and the effective-payload merge route.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.brand_memory import BrandMemoryExample
from app.models.channel import (
    Channel,
    ChannelPlatformValues,
    WorkspaceChannel,
    WorkspaceChannelRoleValues,
)

# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------


async def _login_as(
    client: AsyncClient,
    *,
    email: str,
    password: str = "S3curePass!",
) -> dict[str, Any]:
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
    me = await _login_as(client, email="bm-api@example.com")
    yield client, me


async def _attach_channel_to_default_brand(
    db_session: AsyncSession,
    *,
    workspace_id: str,
    brand_id: str,
    external_id: int = 909_090_909,
) -> str:
    """Insert a Channel + WorkspaceChannel binding; return the binding id."""

    channel = Channel(
        platform=ChannelPlatformValues.TELEGRAM,
        external_id=external_id,
        username=f"ch{external_id}",
        title="ch",
        is_public=True,
    )
    db_session.add(channel)
    await db_session.flush()

    binding = WorkspaceChannel(
        workspace_id=uuid.UUID(workspace_id),
        brand_id=uuid.UUID(brand_id),
        channel_id=channel.id,
        role=WorkspaceChannelRoleValues.OWNED,
    )
    db_session.add(binding)
    await db_session.commit()
    return str(binding.id)


# ---------------------------------------------------------------------------
# GET / PATCH /memory/core
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_core_returns_empty_payload_with_version_1(
    authed: tuple[AsyncClient, dict[str, Any]],
) -> None:
    client, _ = authed
    brand_id = (await client.get("/v1/brands")).json()[0]["id"]
    resp = await client.get(f"/v1/brands/{brand_id}/memory/core")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["brand_id"] == brand_id
    assert body["version"] == 1
    payload = body["payload"]
    # Empty defaults: every list empty, every scalar None, no extras.
    assert payload["taboos"] == []
    assert payload["keywords"] == []
    assert payload["post_types"] == []
    assert payload["extras"] == {}
    assert payload["tone"] is None
    assert payload["audience"] is None


@pytest.mark.asyncio
async def test_patch_core_replaces_payload_and_bumps_version(
    authed: tuple[AsyncClient, dict[str, Any]],
) -> None:
    client, _ = authed
    brand_id = (await client.get("/v1/brands")).json()[0]["id"]
    # Prime the row so we have a deterministic v1 to PATCH against.
    await client.get(f"/v1/brands/{brand_id}/memory/core")

    resp = await client.patch(
        f"/v1/brands/{brand_id}/memory/core",
        json={
            "if_match_version": 1,
            "payload": {
                "taboos": ["never claim 100%"],
                "keywords": ["ai", "social"],
            },
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["version"] == 2
    assert body["payload"]["taboos"] == ["never claim 100%"]
    assert body["payload"]["keywords"] == ["ai", "social"]


@pytest.mark.asyncio
async def test_patch_core_stale_version_returns_409(
    authed: tuple[AsyncClient, dict[str, Any]],
) -> None:
    client, _ = authed
    brand_id = (await client.get("/v1/brands")).json()[0]["id"]
    await client.get(f"/v1/brands/{brand_id}/memory/core")
    # Bump once to v2.
    await client.patch(
        f"/v1/brands/{brand_id}/memory/core",
        json={"if_match_version": 1, "payload": {"keywords": ["a"]}},
    )
    # Re-PATCH with stale ``if_match_version=1`` must conflict.
    resp = await client.patch(
        f"/v1/brands/{brand_id}/memory/core",
        json={"if_match_version": 1, "payload": {"keywords": ["b"]}},
    )
    assert resp.status_code == 409
    assert resp.json()["error_code"] == "BRAND_MEMORY_VERSION_CONFLICT"


@pytest.mark.asyncio
async def test_get_core_unknown_brand_returns_403(
    authed: tuple[AsyncClient, dict[str, Any]],
) -> None:
    client, _ = authed
    bogus = "11111111-2222-3333-4444-555555555555"
    resp = await client.get(f"/v1/brands/{bogus}/memory/core")
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "BRAND_NOT_IN_WORKSPACE"


@pytest.mark.asyncio
async def test_patch_core_rejects_unknown_top_level_key(
    authed: tuple[AsyncClient, dict[str, Any]],
) -> None:
    client, _ = authed
    brand_id = (await client.get("/v1/brands")).json()[0]["id"]
    await client.get(f"/v1/brands/{brand_id}/memory/core")
    resp = await client.patch(
        f"/v1/brands/{brand_id}/memory/core",
        json={
            "if_match_version": 1,
            "payload": {"unknown_key": "rejected"},
        },
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET / PATCH /memory/overlays
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_overlay_materialises_empty_row_for_bound_channel(
    authed: tuple[AsyncClient, dict[str, Any]],
    db_session: AsyncSession,
) -> None:
    client, me = authed
    brand_id = (await client.get("/v1/brands")).json()[0]["id"]
    ws_channel_id = await _attach_channel_to_default_brand(
        db_session,
        workspace_id=me["active_workspace"]["id"],
        brand_id=brand_id,
    )
    resp = await client.get(
        f"/v1/brands/{brand_id}/memory/overlays/{ws_channel_id}",
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["workspace_channel_id"] == ws_channel_id
    assert body["version"] == 1
    assert body["payload"]["keywords"] == []


@pytest.mark.asyncio
async def test_get_overlay_unbound_channel_returns_404(
    authed: tuple[AsyncClient, dict[str, Any]],
) -> None:
    client, _ = authed
    brand_id = (await client.get("/v1/brands")).json()[0]["id"]
    bogus = "22222222-3333-4444-5555-666666666666"
    resp = await client.get(f"/v1/brands/{brand_id}/memory/overlays/{bogus}")
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "BRAND_MEMORY_CHANNEL_NOT_BOUND"


@pytest.mark.asyncio
async def test_patch_overlay_round_trip(
    authed: tuple[AsyncClient, dict[str, Any]],
    db_session: AsyncSession,
) -> None:
    client, me = authed
    brand_id = (await client.get("/v1/brands")).json()[0]["id"]
    ws_channel_id = await _attach_channel_to_default_brand(
        db_session,
        workspace_id=me["active_workspace"]["id"],
        brand_id=brand_id,
    )
    # Materialise the row via GET so subsequent PATCH starts from v1.
    await client.get(f"/v1/brands/{brand_id}/memory/overlays/{ws_channel_id}")

    resp = await client.patch(
        f"/v1/brands/{brand_id}/memory/overlays/{ws_channel_id}",
        json={
            "if_match_version": 1,
            "payload": {"keywords": ["channel-only"]},
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["version"] == 2
    assert body["payload"]["keywords"] == ["channel-only"]


# ---------------------------------------------------------------------------
# GET /memory/effective
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_effective_without_channel_returns_core_payload_only(
    authed: tuple[AsyncClient, dict[str, Any]],
) -> None:
    client, _ = authed
    brand_id = (await client.get("/v1/brands")).json()[0]["id"]
    await client.get(f"/v1/brands/{brand_id}/memory/core")
    await client.patch(
        f"/v1/brands/{brand_id}/memory/core",
        json={"if_match_version": 1, "payload": {"keywords": ["core-only"]}},
    )

    resp = await client.get(f"/v1/brands/{brand_id}/memory/effective")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["workspace_channel_id"] is None
    assert body["overlay_version"] is None
    assert body["core_version"] == 2
    assert body["payload"]["keywords"] == ["core-only"]


@pytest.mark.asyncio
async def test_effective_with_channel_merges_overlay_over_core(
    authed: tuple[AsyncClient, dict[str, Any]],
    db_session: AsyncSession,
) -> None:
    client, me = authed
    brand_id = (await client.get("/v1/brands")).json()[0]["id"]
    ws_channel_id = await _attach_channel_to_default_brand(
        db_session,
        workspace_id=me["active_workspace"]["id"],
        brand_id=brand_id,
    )
    await client.get(f"/v1/brands/{brand_id}/memory/core")
    await client.patch(
        f"/v1/brands/{brand_id}/memory/core",
        json={
            "if_match_version": 1,
            "payload": {
                "keywords": ["core-1"],
                "taboos": ["never"],
            },
        },
    )
    await client.get(f"/v1/brands/{brand_id}/memory/overlays/{ws_channel_id}")
    await client.patch(
        f"/v1/brands/{brand_id}/memory/overlays/{ws_channel_id}",
        json={
            "if_match_version": 1,
            "payload": {"keywords": ["channel-only"]},
        },
    )

    resp = await client.get(
        f"/v1/brands/{brand_id}/memory/effective",
        params={"workspaceChannelId": ws_channel_id},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["workspace_channel_id"] == ws_channel_id
    # ``keywords`` is overridden by the overlay; ``taboos`` falls
    # through from the core untouched.
    assert body["payload"]["keywords"] == ["channel-only"]
    assert body["payload"]["taboos"] == ["never"]


# ---------------------------------------------------------------------------
# GET /memory/examples
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_examples_returns_paginated_envelope(
    authed: tuple[AsyncClient, dict[str, Any]],
    db_session: AsyncSession,
) -> None:
    client, me = authed
    brand_id = (await client.get("/v1/brands")).json()[0]["id"]
    workspace_id = uuid.UUID(me["active_workspace"]["id"])

    zeros = [0.0] * 1536
    for idx in range(2):
        db_session.add(
            BrandMemoryExample(
                workspace_id=workspace_id,
                brand_id=uuid.UUID(brand_id),
                model="mock:em",
                text_snippet=f"sample-{idx}",
                embedding=zeros,
            ),
        )
    await db_session.commit()

    resp = await client.get(
        f"/v1/brands/{brand_id}/memory/examples",
        params={"limit": 10, "offset": 0},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2
    assert {row["text_snippet"] for row in body["items"]} == {
        "sample-0",
        "sample-1",
    }
