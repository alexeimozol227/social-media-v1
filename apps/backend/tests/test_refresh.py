"""Tests for /v1/auth/refresh rotation + replay detection."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


async def _register_and_login(client: AsyncClient) -> tuple[str, str]:
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
    assert resp.status_code == 200
    return resp.json()["access_token"], resp.cookies["sm_refresh"]


@pytest.mark.asyncio
async def test_refresh_rotates_token(client: AsyncClient) -> None:
    _, refresh_token = await _register_and_login(client)
    resp = await client.post(
        "/v1/auth/refresh",
        cookies={"sm_refresh": refresh_token},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["access_token"]
    new_refresh = resp.cookies.get("sm_refresh")
    assert new_refresh and new_refresh != refresh_token


@pytest.mark.asyncio
async def test_refresh_replay_revokes_family(client: AsyncClient) -> None:
    """Presenting an already-rotated refresh token wipes the whole family."""

    _, first_refresh = await _register_and_login(client)

    # Rotate once.
    ok = await client.post(
        "/v1/auth/refresh",
        cookies={"sm_refresh": first_refresh},
    )
    assert ok.status_code == 200
    second_refresh = ok.cookies["sm_refresh"]

    # Replay the OLD refresh — should 403.
    replay = await client.post(
        "/v1/auth/refresh",
        cookies={"sm_refresh": first_refresh},
    )
    assert replay.status_code == 403
    assert replay.json()["error_code"] == "REFRESH_TOKEN_REPLAYED"

    # The whole family is now revoked: even the legitimate second
    # token can't refresh.
    blocked = await client.post(
        "/v1/auth/refresh",
        cookies={"sm_refresh": second_refresh},
    )
    assert blocked.status_code == 403
    assert blocked.json()["error_code"] == "REFRESH_TOKEN_REPLAYED"


@pytest.mark.asyncio
async def test_refresh_missing_cookie_401(client: AsyncClient) -> None:
    resp = await client.post("/v1/auth/refresh")
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "INVALID_REFRESH_TOKEN"


@pytest.mark.asyncio
async def test_refresh_invalid_value_401(client: AsyncClient) -> None:
    resp = await client.post(
        "/v1/auth/refresh",
        cookies={"sm_refresh": "this-is-not-a-real-token"},
    )
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "INVALID_REFRESH_TOKEN"
