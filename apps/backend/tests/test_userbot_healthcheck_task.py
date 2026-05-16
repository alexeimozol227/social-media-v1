"""Tests for the :mod:`app.workers.tasks.userbot_healthcheck` Celery task.

The task body calls :func:`asyncio.run`, so the test functions must
be synchronous — the seeding fixture uses ``pytest_asyncio`` and
commits before yielding so the task's own event loop can observe
the row through a fresh :class:`AsyncSession`.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from celery.exceptions import Retry  # type: ignore[import-untyped]
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.userbot import (
    UserBotAuthError,
    UserBotTransportError,
)
from app.core import crypto
from app.models.telegram_userbot_session import TelegramUserbotSession
from app.services.userbot_sessions import register_session
from app.workers.tasks import userbot_healthcheck as task_module

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _bind_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Provide a fresh Fernet key for the entire test module."""

    monkeypatch.setattr(
        crypto.settings,
        "userbot_encryption_key",
        Fernet.generate_key().decode(),
    )
    crypto._get_cipher.cache_clear()
    yield
    crypto._get_cipher.cache_clear()


@pytest_asyncio.fixture
async def seed_session(db_session: AsyncSession) -> uuid.UUID:
    """Seed one user-bot session row and commit so the task can read it."""

    row = await register_session(
        db_session,
        phone_number="+15550009999",
        account_label="bot-hc",
        api_id=42,
        api_hash="aabbccddeeff00112233445566778899",
        session_string="session-string",
    )
    await db_session.commit()
    return row.id


@pytest.fixture
def patched_task_session(
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Point the task's :data:`AsyncSessionLocal` at the test factory."""

    monkeypatch.setattr(task_module, "AsyncSessionLocal", db_session_factory)
    yield


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_task_invalid_uuid_returns_skipped(
    patched_task_session: None,
) -> None:
    del patched_task_session
    result = task_module.healthcheck_session.apply(args=["not-a-uuid"]).get(
        disable_sync_subtasks=False,
    )
    assert result["skipped"] == "invalid_uuid"


def test_task_unknown_session_id_returns_skipped(
    patched_task_session: None,
) -> None:
    del patched_task_session
    result = task_module.healthcheck_session.apply(args=[str(uuid.uuid4())]).get(
        disable_sync_subtasks=False,
    )
    assert result["skipped"] == "unknown_session"


def test_task_happy_path_updates_healthcheck_columns(
    seed_session: uuid.UUID,
    patched_task_session: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Default ``settings.userbot_client="mock"`` returns ``True``."""

    del patched_task_session

    result = task_module.healthcheck_session.apply(args=[str(seed_session)]).get(
        disable_sync_subtasks=False,
    )
    assert result["result"] == "ok"

    async def _check() -> TelegramUserbotSession | None:
        async with db_session_factory() as fresh:
            return await fresh.get(TelegramUserbotSession, seed_session)

    row = asyncio.run(_check())
    assert row is not None
    assert row.last_healthcheck_ok is True
    assert row.last_healthcheck_at is not None


def test_task_auth_error_marks_banned_no_retry(
    seed_session: uuid.UUID,
    patched_task_session: None,
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del patched_task_session

    bad_client = MagicMock()
    bad_client.healthcheck = AsyncMock(side_effect=UserBotAuthError("revoked"))
    bad_client.close = AsyncMock()
    monkeypatch.setattr(
        task_module,
        "build_default_userbot_client",
        lambda **_kwargs: bad_client,
    )

    result = task_module.healthcheck_session.apply(args=[str(seed_session)]).get(
        disable_sync_subtasks=False,
    )
    assert result["result"] == "banned"

    async def _check() -> TelegramUserbotSession | None:
        async with db_session_factory() as fresh:
            return await fresh.get(TelegramUserbotSession, seed_session)

    row = asyncio.run(_check())
    assert row is not None
    assert row.status == "banned"


def test_task_transport_error_retries(
    seed_session: uuid.UUID,
    patched_task_session: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Transient :class:`UserBotTransportError` triggers ``self.retry`` —
    in Celery eager mode the retry loop exhausts to either ``Retry``
    or the original exception (PR #17 task suite asserts the same
    invariant)."""

    del patched_task_session

    healthcheck_calls: list[None] = []
    bad_client = MagicMock()

    async def _failing() -> bool:
        healthcheck_calls.append(None)
        raise UserBotTransportError("connection reset")

    bad_client.healthcheck = _failing
    bad_client.close = AsyncMock()
    monkeypatch.setattr(
        task_module,
        "build_default_userbot_client",
        lambda **_kwargs: bad_client,
    )

    eager_result = task_module.healthcheck_session.apply(args=[str(seed_session)])
    assert eager_result.failed()
    assert isinstance(eager_result.result, Retry | UserBotTransportError)
    assert len(healthcheck_calls) >= 2
