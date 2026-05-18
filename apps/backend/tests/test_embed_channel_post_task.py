"""Unit tests for the ``channel.embed_post`` Celery task (PR #20).

The task body invokes the async :class:`EmbeddingsService` via
``asyncio.run`` and a fresh :class:`AsyncSession`. Tests patch the
session factory + provider builder so the task runs against the
in-memory SQLite fixture.

Coverage:

* Happy path returns the persist result envelope.
* Invalid UUID input doesn't crash the worker.
* Unknown post id surfaces as ``skipped="unknown_post"``.
* Transient :class:`LLMTimeoutError` triggers Celery retry.
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
    ChatMessage,
    ChatResponse,
    LLMProvider,
    LLMTimeoutError,
    MockLLMProvider,
    ProviderHealth,
    ResponseFormat,
    ToolSpec,
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


@pytest_asyncio.fixture
async def seed_post(
    db_session: AsyncSession,
) -> ChannelPost:
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
    monkeypatch.setattr(task_module, "AsyncSessionLocal", db_session_factory)
    yield


@pytest.fixture
def patched_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    def _factory() -> LLMProvider:
        return MockLLMProvider(dim=4)

    monkeypatch.setattr(task_module, "build_default_provider", _factory)
    monkeypatch.setattr(settings, "embedding_dim", 4)
    monkeypatch.setattr(settings, "embedding_model", "text-embedding-3-small")
    yield


def test_task_happy_path_returns_inserted_envelope(
    seed_post: ChannelPost,
    patched_task_session: None,
    patched_provider: None,
) -> None:
    del patched_task_session, patched_provider
    result = task_module.embed_channel_post.apply(args=[str(seed_post.id)])
    payload = result.get(disable_sync_subtasks=False)

    assert payload["inserted"] is True
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


def test_task_retries_on_transient_error(
    seed_post: ChannelPost,
    monkeypatch: pytest.MonkeyPatch,
    patched_task_session: None,
) -> None:
    del patched_task_session

    embed_calls: list[list[str]] = []

    class TimeoutProvider:
        provider_slug = "test"

        async def chat(
            self,
            messages: list[ChatMessage],
            model: str,
            *,
            tools: list[ToolSpec] | None = None,
            response_format: ResponseFormat | None = None,
            temperature: float = 0.2,
            max_tokens: int = 2000,
            idempotency_key: str | None = None,
        ) -> ChatResponse:  # pragma: no cover - not exercised
            del messages, model, tools, response_format
            del temperature, max_tokens, idempotency_key
            return ChatResponse()

        async def embed(
            self,
            texts: list[str],
            model: str = "text-embedding-3-small",
        ) -> list[list[float]]:
            del model
            embed_calls.append(list(texts))
            raise LLMTimeoutError("simulated")

        async def health_check(self) -> ProviderHealth:  # pragma: no cover
            return ProviderHealth(provider=self.provider_slug, status="ok")

    def _factory() -> LLMProvider:
        return TimeoutProvider()

    monkeypatch.setattr(task_module, "build_default_provider", _factory)
    monkeypatch.setattr(settings, "embedding_dim", 4)
    monkeypatch.setattr(settings, "embedding_model", "text-embedding-3-small")

    eager_result = task_module.embed_channel_post.apply(args=[str(seed_post.id)])
    assert eager_result.failed()
    assert isinstance(eager_result.result, Retry | LLMTimeoutError)
    assert len(embed_calls) >= 2


_unused: tuple[type, ...] = (AsyncIterator, type(Any))
del _unused
