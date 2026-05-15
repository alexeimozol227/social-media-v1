"""Tests for /v1/auth/logout + /v1/auth/me."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


async def _bootstrap(client: AsyncClient) -> tuple[str, str]:
    await client.post(
        "/v1/auth/register",
        json={
            "email": "alice@example.com",
            "password": "S3curePass!",
            "tos_accepted": True,
        },
    )
    resp = await client.post(
        "/v1/auth/login",
        json={"email": "alice@example.com", "password": "S3curePass!"},
    )
    return resp.json()["access_token"], resp.cookies["sm_refresh"]


@pytest.mark.asyncio
async def test_logout_revokes_family(client: AsyncClient) -> None:
    _, refresh_token = await _bootstrap(client)
    out = await client.post(
        "/v1/auth/logout",
        cookies={"sm_refresh": refresh_token},
    )
    assert out.status_code == 204

    # Refresh after logout should fail.
    fail = await client.post(
        "/v1/auth/refresh",
        cookies={"sm_refresh": refresh_token},
    )
    assert fail.status_code in (401, 403)


@pytest.mark.asyncio
async def test_logout_without_cookies_is_idempotent_204(client: AsyncClient) -> None:
    resp = await client.post("/v1/auth/logout")
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_me_with_bearer_returns_user_and_workspace(client: AsyncClient) -> None:
    access, _ = await _bootstrap(client)
    resp = await client.get(
        "/v1/auth/me",
        headers={"Authorization": f"Bearer {access}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user"]["email"] == "alice@example.com"
    assert body["active_workspace"]["slug"] == "default"
    assert body["active_workspace"]["type"] == "solo"
    # D64: /me hands the SPA the user's memberships from the Redis cache
    # so role-aware UI doesn't need a second round-trip.
    assert isinstance(body["memberships"], list)
    assert len(body["memberships"]) == 1
    assert body["memberships"][0]["workspace_id"] == body["active_workspace"]["id"]
    assert body["memberships"][0]["role"] == "owner"


@pytest.mark.asyncio
async def test_me_without_token_401(client: AsyncClient) -> None:
    resp = await client.get("/v1/auth/me")
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "UNAUTHENTICATED"


@pytest.mark.asyncio
async def test_me_with_garbage_bearer_401(client: AsyncClient) -> None:
    resp = await client.get(
        "/v1/auth/me",
        headers={"Authorization": "Bearer not-a-jwt"},
    )
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "UNAUTHENTICATED"
