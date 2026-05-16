"""Shared pytest fixtures.

Tests run against an in-memory SQLite (aiosqlite) DB so the suite is
hermetic and fast — production uses Postgres via asyncpg. The SQLite
``StaticPool`` keeps one shared connection across the FastAPI deps so
tables created in the fixture are visible to the request handlers.

A ``fakeredis`` async client is injected as the Redis dependency so
the login-lockout policy is exercised end-to-end without spinning up
a real Redis.
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-prod-min-32-chars")
os.environ.setdefault("ENVIRONMENT", "test")

from collections.abc import AsyncIterator
from typing import Any

import fakeredis.aioredis
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.models import (  # noqa: F401
    AuditEvent,
    Brand,
    Channel,
    ChannelPost,
    EmailVerification,
    IdempotencyKey,
    Invoice,
    PasswordReset,
    Plan,
    PlanPrice,
    RefreshToken,
    TenantLimitOverride,
    User,
    Workspace,
    WorkspaceChannel,
    WorkspaceMember,
)


@pytest_asyncio.fixture
async def db_engine() -> AsyncIterator[Any]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session_factory(db_engine: Any) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )


@pytest_asyncio.fixture
async def db_session(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with db_session_factory() as session:
        yield session


@pytest.fixture
def fake_redis() -> fakeredis.aioredis.FakeRedis:
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


class _CapturingEmailSender:
    """Test double for :class:`app.core.email.EmailSender`.

    Captures every send call into ``sent`` so tests can assert on
    the subject / body / purpose. Body is captured verbatim because
    the verification code lives inside it — the production transports
    deliberately don't log it.
    """

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        purpose: str | None = None,
    ) -> None:
        self.sent.append(
            {
                "to": to,
                "subject": subject,
                "body": body,
                "purpose": purpose,
            }
        )


@pytest.fixture
def email_sender() -> _CapturingEmailSender:
    return _CapturingEmailSender()


@pytest_asyncio.fixture
async def client(
    db_session_factory: async_sessionmaker[AsyncSession],
    fake_redis: fakeredis.aioredis.FakeRedis,
    email_sender: _CapturingEmailSender,
) -> AsyncIterator[AsyncClient]:
    """An ``httpx.AsyncClient`` bound to a freshly-wired FastAPI app.

    The DB session, Redis client, and email sender are all overridden
    so the test runs hermetically.
    """

    from app.api.routes.auth import get_redis as _routes_get_redis  # noqa: F401
    from app.core import redis as redis_module
    from app.core.email import get_email_sender
    from app.main import app

    async def _override_get_db() -> AsyncIterator[AsyncSession]:
        async with db_session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_email_sender] = lambda: email_sender
    # Patch the singleton Redis getter so ``services.auth`` + routes
    # see the fake.
    original_getter = redis_module._redis
    redis_module._redis = fake_redis
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()
        redis_module._redis = original_getter
