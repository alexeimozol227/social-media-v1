"""Tests for the Idempotency middleware (PR #8, П13 in docs/04).

Exercises:
  * POST with ``Idempotency-Key`` → first call executes, second
    returns cached response.
  * GET requests are never idempotency-gated.
  * Missing header → normal processing (no caching).
  * Key too long → 422.
  * Error responses (4xx) are NOT cached.
"""

from __future__ import annotations

from httpx import AsyncClient

# -- helper: register + login to get an access token ----

async def _register_and_login(client: AsyncClient) -> str:
    """Create a user, log in, and return the Bearer token."""
    await client.post(
        "/v1/auth/register",
        json={
            "email": "idem@example.com",
            "password": "StrongPass1!",
            "full_name": "Idem User",
            "tos_accepted": True,
        },
    )
    r = await client.post(
        "/v1/auth/login",
        json={"email": "idem@example.com", "password": "StrongPass1!"},
    )
    return f"Bearer {r.json()['access_token']}"


# -- tests ----


async def test_idempotent_post_returns_cached_on_second_call(
    client: AsyncClient,
) -> None:
    """Two POSTs with the same key should produce identical responses."""
    auth = await _register_and_login(client)
    headers = {"Authorization": auth, "Idempotency-Key": "key-register-1"}

    r1 = await client.post(
        "/v1/auth/register",
        json={
            "email": "new-user@example.com",
            "password": "StrongPass1!",
            "full_name": "New User",
            "tos_accepted": True,
        },
        headers=headers,
    )
    assert r1.status_code == 201

    # Second call with the same key → should return cached 201 body.
    r2 = await client.post(
        "/v1/auth/register",
        json={
            "email": "new-user-2@example.com",
            "password": "StrongPass1!",
            "full_name": "Different User",
            "tos_accepted": True,
        },
        headers=headers,
    )
    assert r2.status_code == 201
    assert r2.json() == r1.json()


async def test_get_request_ignores_idempotency_header(
    client: AsyncClient,
) -> None:
    """GET is a safe method → header should be ignored."""
    r1 = await client.get("/", headers={"Idempotency-Key": "get-key-1"})
    r2 = await client.get("/", headers={"Idempotency-Key": "get-key-1"})
    assert r1.status_code == 200
    assert r2.status_code == 200


async def test_post_without_header_processes_normally(
    client: AsyncClient,
) -> None:
    """No ``Idempotency-Key`` → request is processed normally."""
    r = await client.post(
        "/v1/auth/register",
        json={
            "email": "no-idem@example.com",
            "password": "StrongPass1!",
            "full_name": "No Idem",
            "tos_accepted": True,
        },
    )
    assert r.status_code == 201


async def test_key_too_long_returns_422(
    client: AsyncClient,
) -> None:
    long_key = "x" * 300
    r = await client.post(
        "/v1/auth/register",
        json={
            "email": "toolong@example.com",
            "password": "StrongPass1!",
            "full_name": "Too Long",
            "tos_accepted": True,
        },
        headers={"Idempotency-Key": long_key},
    )
    assert r.status_code == 422
    assert r.json()["error_code"] == "IDEMPOTENCY_KEY_TOO_LONG"


async def test_error_responses_not_cached(
    client: AsyncClient,
) -> None:
    """A 409 (duplicate email) should NOT be cached."""
    # First register succeeds.
    await client.post(
        "/v1/auth/register",
        json={
            "email": "dup@example.com",
            "password": "StrongPass1!",
            "full_name": "First",
            "tos_accepted": True,
        },
    )

    headers = {"Idempotency-Key": "dup-email-key"}
    r1 = await client.post(
        "/v1/auth/register",
        json={
            "email": "dup@example.com",
            "password": "StrongPass1!",
            "full_name": "Second",
            "tos_accepted": True,
        },
        headers=headers,
    )
    assert r1.status_code == 409

    # Same key again should still hit the real handler (409 not cached).
    r2 = await client.post(
        "/v1/auth/register",
        json={
            "email": "dup@example.com",
            "password": "StrongPass1!",
            "full_name": "Third",
            "tos_accepted": True,
        },
        headers=headers,
    )
    assert r2.status_code == 409


async def test_different_keys_not_shared(
    client: AsyncClient,
) -> None:
    """Different idempotency keys produce independent caches."""
    r1 = await client.post(
        "/v1/auth/register",
        json={
            "email": "user-a@example.com",
            "password": "StrongPass1!",
            "full_name": "A",
            "tos_accepted": True,
        },
        headers={"Idempotency-Key": "key-a"},
    )
    assert r1.status_code == 201

    r2 = await client.post(
        "/v1/auth/register",
        json={
            "email": "user-b@example.com",
            "password": "StrongPass1!",
            "full_name": "B",
            "tos_accepted": True,
        },
        headers={"Idempotency-Key": "key-b"},
    )
    assert r2.status_code == 201
    assert r2.json()["email"] == "user-b@example.com"
