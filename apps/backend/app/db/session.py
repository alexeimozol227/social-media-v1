"""Async DB engine and session factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

# SQLite (used in tests) doesn't support pool_size / max_overflow.
_engine_kwargs: dict[str, Any] = {"echo": False, "pool_pre_ping": True}
if not settings.database_url.startswith("sqlite"):
    _engine_kwargs.update({"pool_size": 10, "max_overflow": 20})

engine = create_async_engine(settings.database_url, **_engine_kwargs)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency that yields a DB session."""

    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
