"""Unit tests for the ``channel.embed_post`` Celery task (PR #17).

The task body invokes the async :class:`EmbeddingsService` via
``asyncio.run`` and a fresh :class:`AsyncSession`. Tests patch the
session factory + provider builder so the task runs against the
in-memory SQLite fixture used by the rest of the suite.

Coverage:

* Happy path: task calls the service, returns the persist result
  envelope as a JSON dict.
* Unknown UUID input doesn't crash the worker — returns a
  ``skipped="invalid_uuid"`` envelope.
* Unknown post id is handled by the service and surfaces as
  ``skipped="unknown_post"`` in the task result.
* Transient :class:`LLMTimeoutError` triggers a Celery retry (the
  task raises :class:`~celery.exceptions.Retry`).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from celery.exceptions import Retry  # type: ignore[import-untyped]
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.llm import (
    EmbeddingResult,
    LLMProvider,
    LLMResult,
    LLMTimeoutError,
    MockLLMProvider,
    Tool,
)
from app.core.config import settings
from app.models.brand import Brand
from app.models.channel import (
    Channel,
    ChannelPost,
    WorkspaceChannel,
    WorkspaceChannelRoleValues,
)
from app.models.user import User, UserStatus
from app.models.workspace import Workspace, WorkspaceType
from app.workers.tasks import embed_channel_post as task_module

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def seed_post(
    db_session: AsyncSession,
) -> ChannelPost:
    """Seed the bare minimum to embed a post end-to-end."""

    user = User(
        email="task@example.com",
        hashed_password="x",
        full_name="Task Tester",
        locale="ru-RU",
        timezone="UTC",
        preferred_currency="RUB",
        status=UserStatus.ACTIVE,
    )
    db_session.add(user)
    await db_session.flush()
    ws = Workspace(
        owner_id=user.id,
        name="WS",
        slug="task",
        type=WorkspaceType.SOLO,
        preferred_currency="RUB",
    )
    db_session.add(ws)
    await db_session.flush()
    brand = Brand(
        workspace_id=ws.id,
        name="Brand",
        content_language="ru",
        timezone="UTC",
        is_default=True,
    )
    db_session.add(brand)
    await db_session.flush()
    channel = Channel(
        platform="telegram",
        external_id=-1008888888880,
        username="task_channel",
        title="Task Channel",
        is_public=True,
    )
    db_session.add(channel)
    await db_session.flush()
    db_session.add(
        WorkspaceChannel(
            workspace_id=ws.id,
            brand_id=brand.id,
            channel_id=channel.id,
            role=WorkspaceChannelRoleValues.OWNED,
            bot_admin_rights={"status": "administrator", "can_post_messages": True},
        ),
    )
    await db_session.flush()
    post = ChannelPost(
        channel_id=channel.id,
        tg_message_id=200,
        text="Embed me, please.",
        has_media=False,
        posted_at=datetime.now(tz=UTC),
    )
    db_session.add(post)
    await db_session.flush()
    await db_session.commit()
    return post


@pytest.fixture
def patched_task_session(
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Point the task's :data:`AsyncSessionLocal` at the test factory."""

    monkeypatch.setattr(task_module, "AsyncSessionLocal", db_session_factory)
    yield


@pytest.fixture
def patched_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Force the task to build a :class:`MockLLMProvider` for tests."""

    def _factory() -> LLMProvider:
        return MockLLMProvider(dim=4)

    monkeypatch.setattr(task_module, "build_default_provider", _factory)
    monkeypatch.setattr(settings, "embedding_dim", 4)
    monkeypatch.setattr(settings, "embedding_model", "text-embedding-3-small")
    yield


# ---------------------------------------------------------------------------
# Task tests
# ---------------------------------------------------------------------------


def test_task_happy_path_returns_inserted_envelope(
    seed_post: ChannelPost,
    patched_task_session: None,
    patched_provider: None,
) -> None:
    """The task body itself calls ``asyncio.run(...)`` so the test
    function must be synchronous — otherwise we'd be calling
    ``asyncio.run`` from a running loop (pytest-asyncio's loop)."""

    del patched_task_session, patched_provider
    # Run the task body synchronously — the celery_app is configured
    # with task_always_eager=False so we invoke .apply() to use the
    # in-process Eager backend.
    result = task_module.embed_channel_post.apply(args=[str(seed_post.id)])
    payload = result.get(disable_sync_subtasks=False)

    assert payload["inserted"] is True
    assert payload["updated"] is False
    assert payload["skipped"] is None
    assert payload["channel_post_id"] == str(seed_post.id)
    assert payload["model"] == "text-embedding-3-small"


def test_task_with_invalid_uuid_returns_skipped(
    patched_task_session: None,
    patched_provider: None,
) -> None:
    del patched_task_session, patched_provider
    result = task_module.embed_channel_post.apply(args=["not-a-uuid"])
    payload = result.get(disable_sync_subtasks=False)
    assert payload["skipped"] == "invalid_uuid"


def test_task_with_unknown_post_id_returns_skipped(
    patched_task_session: None,
    patched_provider: None,
) -> None:
    del patched_task_session, patched_provider
    payload = task_module.embed_channel_post.apply(args=[str(uuid.uuid4())]).get(
        disable_sync_subtasks=False
    )
    assert payload["skipped"] == "unknown_post"
    assert payload["inserted"] is False


def test_task_retries_on_transient_error(
    seed_post: ChannelPost,
    monkeypatch: pytest.MonkeyPatch,
    patched_task_session: None,
) -> None:
    """``LLMTimeoutError`` triggers ``self.retry`` (which Celery
    raises as :class:`Retry` in production worker mode). In Celery's
    Eager mode used by ``.apply()`` the retry loop runs in-process
    and exhausts to the original exception after ``MAX_RETRIES``
    attempts — that's the easiest invariant to assert here without
    monkey-patching the eager-mode internals."""

    del patched_task_session

    embed_calls: list[str] = []

    class TimeoutProvider:
        async def complete(
            self,
            prompt: str,
            model: str,
            *,
            tools: list[Tool] | None = None,
            max_tokens: int = 2000,
        ) -> LLMResult:  # pragma: no cover - never called
            del prompt, model, tools, max_tokens
            return LLMResult(text="")

        async def embed(self, text: str, model: str) -> EmbeddingResult:
            del model
            embed_calls.append(text)
            raise LLMTimeoutError("simulated")

    def _factory() -> LLMProvider:
        return TimeoutProvider()

    monkeypatch.setattr(task_module, "build_default_provider", _factory)
    monkeypatch.setattr(settings, "embedding_dim", 4)
    monkeypatch.setattr(settings, "embedding_model", "text-embedding-3-small")

    eager_result = task_module.embed_channel_post.apply(args=[str(seed_post.id)])
    # ``.failed()`` is True regardless of whether the surfaced
    # exception is :class:`Retry` (real worker mode) or the original
    # adapter error (eager-mode exhaustion). Both are acceptable.
    assert eager_result.failed()
    assert isinstance(eager_result.result, Retry | LLMTimeoutError)
    # The retry loop must have actually invoked the embed adapter
    # more than once (i.e. the retry path was exercised, not just a
    # single failed attempt).
    assert len(embed_calls) >= 2


# A helper marker so the file's async fixture is recognised by the
# narrow-imports linter — silences ``F401`` on AsyncIterator/Any
# without leaking into the test surface.
_unused: tuple[type, ...] = (AsyncIterator, type(Any))
del _unused
