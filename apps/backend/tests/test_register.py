"""Tests for /v1/auth/register."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.brand import Brand
from app.models.user import User
from app.models.workspace import Workspace
from app.models.workspace_member import WorkspaceMember, WorkspaceMemberRole


@pytest.mark.asyncio
async def test_register_success_creates_user_workspace_brand_membership(
    client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    resp = await client.post(
        "/v1/auth/register",
        json={
            "email": "alice@example.com",
            "password": "S3curePass!",
            "full_name": "Alice",
            "tos_accepted": True,
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["email"] == "alice@example.com"
    assert body["full_name"] == "Alice"
    assert body["status"] == "active"
    assert body["platform_role"] == "user"
    assert body["locale"] == "ru-RU"
    assert body["timezone"] == "Europe/Minsk"
    assert body["preferred_currency"] == "RUB"

    # Side effects: workspace + brand + owner membership.
    async with db_session_factory() as session:
        users = (await session.execute(select(User))).scalars().all()
        assert len(users) == 1
        workspaces = (await session.execute(select(Workspace))).scalars().all()
        assert len(workspaces) == 1
        assert workspaces[0].owner_id == users[0].id
        assert workspaces[0].slug == "default"
        members = (await session.execute(select(WorkspaceMember))).scalars().all()
        assert len(members) == 1
        assert members[0].role == WorkspaceMemberRole.OWNER
        brands = (await session.execute(select(Brand))).scalars().all()
        assert len(brands) == 1
        assert brands[0].workspace_id == workspaces[0].id


@pytest.mark.asyncio
async def test_register_duplicate_email_409(client: AsyncClient) -> None:
    payload = {
        "email": "bob@example.com",
        "password": "S3curePass!",
        "tos_accepted": True,
    }
    first = await client.post("/v1/auth/register", json=payload)
    assert first.status_code == 201
    second = await client.post("/v1/auth/register", json=payload)
    assert second.status_code == 409
    assert second.json()["error_code"] == "EMAIL_ALREADY_EXISTS"


@pytest.mark.asyncio
async def test_register_without_tos_accepted_422(client: AsyncClient) -> None:
    resp = await client.post(
        "/v1/auth/register",
        json={
            "email": "carol@example.com",
            "password": "S3curePass!",
            "tos_accepted": False,
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error_code"] == "TOS_NOT_ACCEPTED"


@pytest.mark.asyncio
async def test_register_password_too_short_422(client: AsyncClient) -> None:
    resp = await client.post(
        "/v1/auth/register",
        json={
            "email": "dan@example.com",
            "password": "short",
            "tos_accepted": True,
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error_code"] == "VALIDATION_ERROR"
