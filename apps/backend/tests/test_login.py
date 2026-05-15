"""Tests for /v1/auth/login + lockout."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


async def _register(client: AsyncClient, email: str, password: str) -> None:
    resp = await client.post(
        "/v1/auth/register",
        json={
            "email": email,
            "password": password,
            "tos_accepted": True,
        },
    )
    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_login_success(client: AsyncClient) -> None:
    await _register(client, "alice@example.com", "S3curePass!")
    resp = await client.post(
        "/v1/auth/login",
        json={"email": "alice@example.com", "password": "S3curePass!"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["expires_in"] > 0

    # Cookies set: access (HttpOnly), refresh (HttpOnly, path=/v1/auth), csrf (JS-readable)
    cookies = resp.cookies
    assert "sm_access" in cookies
    assert "sm_refresh" in cookies
    assert "sm_csrf" in cookies


@pytest.mark.asyncio
async def test_login_invalid_password_401(client: AsyncClient) -> None:
    await _register(client, "bob@example.com", "S3curePass!")
    resp = await client.post(
        "/v1/auth/login",
        json={"email": "bob@example.com", "password": "WrongPass!"},
    )
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "INVALID_CREDENTIALS"


@pytest.mark.asyncio
async def test_login_unknown_email_401(client: AsyncClient) -> None:
    resp = await client.post(
        "/v1/auth/login",
        json={"email": "ghost@example.com", "password": "Whatever1!"},
    )
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "INVALID_CREDENTIALS"


@pytest.mark.asyncio
async def test_login_lockout_after_threshold(client: AsyncClient) -> None:
    """10 wrong-password attempts in a row → 11th call returns 423."""

    await _register(client, "carol@example.com", "S3curePass!")
    for _ in range(10):
        bad = await client.post(
            "/v1/auth/login",
            json={"email": "carol@example.com", "password": "WrongPass!"},
        )
        assert bad.status_code == 401

    # 11th attempt — even with the right password — is locked.
    locked = await client.post(
        "/v1/auth/login",
        json={"email": "carol@example.com", "password": "S3curePass!"},
    )
    assert locked.status_code == 423
    assert locked.json()["error_code"] == "LOGIN_LOCKED"
    assert "Retry-After" in locked.headers
