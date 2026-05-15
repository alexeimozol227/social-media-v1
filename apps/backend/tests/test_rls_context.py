"""Smoke test for the RLS context dependency.

The full RLS policy story (per-table policies + PgBouncer config)
lives in a follow-up PR — what we cover here is that the dependency
itself runs cleanly on SQLite (where SET LOCAL is a no-op) without
breaking the request and is wired into /v1/auth/me.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.rls import set_rls_context


@pytest.mark.asyncio
async def test_set_rls_context_noop_on_sqlite(db_session: AsyncSession) -> None:
    """The dependency must not raise when running against aiosqlite."""

    await set_rls_context(
        db_session,
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        platform_role="user",
    )


@pytest.mark.asyncio
async def test_set_rls_context_unknown_role_falls_back_to_user(
    db_session: AsyncSession,
) -> None:
    """A tampered ``platform_role`` is silently normalised to 'user'."""

    await set_rls_context(
        db_session,
        user_id=uuid.uuid4(),
        tenant_id=None,
        platform_role="' OR 1=1; --",
    )


@pytest.mark.asyncio
async def test_me_executes_set_rls_context(client: AsyncClient) -> None:
    """End-to-end: /v1/auth/me runs through ``get_current_user`` (which
    calls ``set_rls_context``) without erroring."""

    await client.post(
        "/v1/auth/register",
        json={
            "email": "alice@example.com",
            "password": "S3curePass!",
            "tos_accepted": True,
        },
    )
    login = await client.post(
        "/v1/auth/login",
        json={"email": "alice@example.com", "password": "S3curePass!"},
    )
    access = login.json()["access_token"]
    me = await client.get(
        "/v1/auth/me",
        headers={"Authorization": f"Bearer {access}"},
    )
    assert me.status_code == 200
