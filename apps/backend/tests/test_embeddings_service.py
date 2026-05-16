"""Unit tests for :class:`app.services.embeddings.EmbeddingsService` (PR #17).

Coverage (10 cases):

* Happy-path insert + skip variants (no text / no binding / unknown post).
* Idempotent re-run = update with new vector.
* Multiple workspace bindings — only one embedding row is persisted.
* Dim mismatch surfaces as :class:`LLMProviderError` (configuration bug,
  not a retryable provider error).
* Workspace_id matches the oldest active binding (stable across re-runs).
* Provider error / transient error propagates so the Celery task retries.
* Detached binding (``disconnected_at`` set) is treated as no binding.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.llm import (
    EmbeddingResult,
    LLMProvider,
    LLMProviderError,
    LLMResult,
    LLMTimeoutError,
    MockLLMProvider,
    Tool,
)
from app.models.brand import Brand
from app.models.channel import (
    Channel,
    ChannelPost,
    WorkspaceChannel,
    WorkspaceChannelRoleValues,
)
from app.models.channel_post_embedding import ChannelPostEmbedding
from app.models.user import User, UserStatus
from app.models.workspace import Workspace, WorkspaceType
from app.services.embeddings import (
    SKIP_NO_BINDING,
    SKIP_NO_TEXT,
    SKIP_UNKNOWN_POST,
    EmbeddingsService,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def seed(
    db_session: AsyncSession,
) -> tuple[User, Workspace, Brand, Channel, WorkspaceChannel, ChannelPost]:
    """Seed user + workspace + brand + connected channel + 1 post."""

    user = User(
        email="emb@example.com",
        hashed_password="x",
        full_name="Emb Tester",
        locale="ru-RU",
        timezone="UTC",
        preferred_currency="RUB",
        status=UserStatus.ACTIVE,
    )
    db_session.add(user)
    await db_session.flush()

    workspace = Workspace(
        owner_id=user.id,
        name="WS",
        slug="default",
        type=WorkspaceType.SOLO,
        preferred_currency="RUB",
    )
    db_session.add(workspace)
    await db_session.flush()

    brand = Brand(
        workspace_id=workspace.id,
        name="Brand",
        content_language="ru",
        timezone="UTC",
        is_default=True,
    )
    db_session.add(brand)
    await db_session.flush()

    channel = Channel(
        platform="telegram",
        external_id=-1009999999990,
        username="emb_channel",
        title="Emb Channel",
        is_public=True,
        subscribers_count=100,
    )
    db_session.add(channel)
    await db_session.flush()

    binding = WorkspaceChannel(
        workspace_id=workspace.id,
        brand_id=brand.id,
        channel_id=channel.id,
        role=WorkspaceChannelRoleValues.OWNED,
        bot_admin_rights={
            "status": "administrator",
            "can_post_messages": True,
            "captured_at": datetime.now(tz=UTC).isoformat(),
        },
        connected_at=datetime.now(tz=UTC) - timedelta(days=2),
    )
    db_session.add(binding)
    await db_session.flush()

    post = ChannelPost(
        channel_id=channel.id,
        tg_message_id=42,
        text="Hello, world. This is a post.",
        has_media=False,
        posted_at=datetime.now(tz=UTC),
    )
    db_session.add(post)
    await db_session.flush()
    return user, workspace, brand, channel, binding, post


def _service(dim: int = 4) -> EmbeddingsService:
    provider = MockLLMProvider(dim=dim)
    return EmbeddingsService(
        provider=provider,
        model="text-embedding-3-small",
        dim=dim,
    )


# ---------------------------------------------------------------------------
# Service tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_inserts_embedding(
    db_session: AsyncSession,
    seed: tuple[User, Workspace, Brand, Channel, WorkspaceChannel, ChannelPost],
) -> None:
    _u, workspace, _b, channel, _bind, post = seed
    service = _service(dim=4)

    result = await service.embed_channel_post(db_session, post.id)

    assert result.inserted is True
    assert result.updated is False
    assert result.skipped is None
    assert result.channel_post_id == post.id
    assert result.model == "text-embedding-3-small"

    rows = (
        (
            await db_session.execute(
                select(ChannelPostEmbedding).where(ChannelPostEmbedding.channel_post_id == post.id),
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.workspace_id == workspace.id
    assert row.channel_id == channel.id
    assert row.model == "text-embedding-3-small"
    assert isinstance(row.embedding, list)
    assert len(row.embedding) == 4


@pytest.mark.asyncio
async def test_idempotent_rerun_updates_existing_row(
    db_session: AsyncSession,
    seed: tuple[User, Workspace, Brand, Channel, WorkspaceChannel, ChannelPost],
) -> None:
    _u, _ws, _b, _ch, _bind, post = seed
    # Run 1 uses the seeded mock vectors; run 2 plants a fixture so we
    # can prove the row was overwritten in place.
    service_v1 = _service(dim=4)
    first = await service_v1.embed_channel_post(db_session, post.id)
    assert first.inserted is True

    overrides = {post.text or "": [0.7, 0.6, 0.5, 0.4]}
    provider = MockLLMProvider(dim=4, embedding_fixtures=overrides)
    service_v2 = EmbeddingsService(provider=provider, model="text-embedding-3-small", dim=4)
    second = await service_v2.embed_channel_post(db_session, post.id)

    assert second.inserted is False
    assert second.updated is True

    rows = (
        (
            await db_session.execute(
                select(ChannelPostEmbedding).where(ChannelPostEmbedding.channel_post_id == post.id),
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].embedding == [0.7, 0.6, 0.5, 0.4]


@pytest.mark.asyncio
async def test_skips_post_without_text(
    db_session: AsyncSession,
    seed: tuple[User, Workspace, Brand, Channel, WorkspaceChannel, ChannelPost],
) -> None:
    _u, _ws, _b, channel, _bind, _post = seed
    media_post = ChannelPost(
        channel_id=channel.id,
        tg_message_id=99,
        text=None,
        has_media=True,
        media_summary={"kind": "photo"},
        posted_at=datetime.now(tz=UTC),
    )
    db_session.add(media_post)
    await db_session.flush()

    result = await _service().embed_channel_post(db_session, media_post.id)

    assert result.skipped == SKIP_NO_TEXT
    assert result.inserted is False
    assert result.updated is False
    count_rows = (
        await db_session.execute(
            select(ChannelPostEmbedding).where(
                ChannelPostEmbedding.channel_post_id == media_post.id
            ),
        )
    ).all()
    assert count_rows == []


@pytest.mark.asyncio
async def test_skips_post_with_whitespace_only_text(
    db_session: AsyncSession,
    seed: tuple[User, Workspace, Brand, Channel, WorkspaceChannel, ChannelPost],
) -> None:
    _u, _ws, _b, channel, _bind, _post = seed
    blank_post = ChannelPost(
        channel_id=channel.id,
        tg_message_id=100,
        text="   \n\t  ",
        has_media=False,
        posted_at=datetime.now(tz=UTC),
    )
    db_session.add(blank_post)
    await db_session.flush()

    result = await _service().embed_channel_post(db_session, blank_post.id)

    assert result.skipped == SKIP_NO_TEXT


@pytest.mark.asyncio
async def test_skips_post_with_unknown_id(db_session: AsyncSession) -> None:
    result = await _service().embed_channel_post(db_session, uuid.uuid4())
    assert result.skipped == SKIP_UNKNOWN_POST
    assert result.inserted is False
    assert result.updated is False


@pytest.mark.asyncio
async def test_skips_post_when_no_active_binding(
    db_session: AsyncSession,
    seed: tuple[User, Workspace, Brand, Channel, WorkspaceChannel, ChannelPost],
) -> None:
    _u, _ws, _b, _ch, binding, post = seed
    # Detach the only binding — service should report no_binding.
    binding.disconnected_at = datetime.now(tz=UTC)
    await db_session.flush()

    result = await _service().embed_channel_post(db_session, post.id)

    assert result.skipped == SKIP_NO_BINDING
    assert result.inserted is False


@pytest.mark.asyncio
async def test_dim_mismatch_raises_llm_provider_error(
    db_session: AsyncSession,
    seed: tuple[User, Workspace, Brand, Channel, WorkspaceChannel, ChannelPost],
) -> None:
    _u, _ws, _b, _ch, _bind, post = seed
    # Provider returns dim=2 but the service expects dim=4 → config bug.
    provider = MockLLMProvider(
        dim=2,
        embedding_fixtures={post.text or "": [0.1, 0.2]},
    )
    service = EmbeddingsService(provider=provider, model="text-embedding-3-small", dim=4)

    with pytest.raises(LLMProviderError) as exc:
        await service.embed_channel_post(db_session, post.id)
    assert "dim" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_workspace_id_is_oldest_active_binding(
    db_session: AsyncSession,
    seed: tuple[User, Workspace, Brand, Channel, WorkspaceChannel, ChannelPost],
) -> None:
    """When two workspaces bind the same channel, the older one wins.

    docs/03 §D20 Global Channel Registry — embedding rows pick the
    oldest active binding for ``workspace_id`` so re-runs are stable.
    """

    older_user, older_ws, _b, channel, _bind, post = seed
    # Spin up a second workspace + binding that connected *later*.
    newer_owner = User(
        email="other@example.com",
        hashed_password="x",
        full_name="Other",
        locale="ru-RU",
        timezone="UTC",
        preferred_currency="RUB",
        status=UserStatus.ACTIVE,
    )
    db_session.add(newer_owner)
    await db_session.flush()
    newer_ws = Workspace(
        owner_id=newer_owner.id,
        name="WS2",
        slug="other",
        type=WorkspaceType.SOLO,
        preferred_currency="RUB",
    )
    db_session.add(newer_ws)
    await db_session.flush()
    newer_brand = Brand(
        workspace_id=newer_ws.id,
        name="OtherBrand",
        content_language="ru",
        timezone="UTC",
        is_default=True,
    )
    db_session.add(newer_brand)
    await db_session.flush()
    newer_binding = WorkspaceChannel(
        workspace_id=newer_ws.id,
        brand_id=newer_brand.id,
        channel_id=channel.id,
        role=WorkspaceChannelRoleValues.OWNED,
        bot_admin_rights={"status": "administrator", "can_post_messages": True},
        connected_at=datetime.now(tz=UTC),  # later than seed
    )
    db_session.add(newer_binding)
    await db_session.flush()

    result = await _service().embed_channel_post(db_session, post.id)
    assert result.inserted is True

    rows = (
        (
            await db_session.execute(
                select(ChannelPostEmbedding).where(ChannelPostEmbedding.channel_post_id == post.id),
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    # Older workspace wins — the seeded binding connected 2 days earlier.
    assert rows[0].workspace_id == older_ws.id
    del older_user


@pytest.mark.asyncio
async def test_only_active_bindings_are_considered(
    db_session: AsyncSession,
    seed: tuple[User, Workspace, Brand, Channel, WorkspaceChannel, ChannelPost],
) -> None:
    """A detached older binding doesn't anchor the row over a newer active one."""

    _u, _older_ws, _b, channel, older_binding, post = seed
    # Detach the seeded binding.
    older_binding.disconnected_at = datetime.now(tz=UTC)
    await db_session.flush()

    # New workspace with an active binding.
    newer_owner = User(
        email="active@example.com",
        hashed_password="x",
        full_name="Active",
        locale="ru-RU",
        timezone="UTC",
        preferred_currency="RUB",
        status=UserStatus.ACTIVE,
    )
    db_session.add(newer_owner)
    await db_session.flush()
    newer_ws = Workspace(
        owner_id=newer_owner.id,
        name="Active WS",
        slug="active",
        type=WorkspaceType.SOLO,
        preferred_currency="RUB",
    )
    db_session.add(newer_ws)
    await db_session.flush()
    newer_brand = Brand(
        workspace_id=newer_ws.id,
        name="ActiveBrand",
        content_language="ru",
        timezone="UTC",
        is_default=True,
    )
    db_session.add(newer_brand)
    await db_session.flush()
    db_session.add(
        WorkspaceChannel(
            workspace_id=newer_ws.id,
            brand_id=newer_brand.id,
            channel_id=channel.id,
            role=WorkspaceChannelRoleValues.OWNED,
            bot_admin_rights={
                "status": "administrator",
                "can_post_messages": True,
            },
        ),
    )
    await db_session.flush()

    result = await _service().embed_channel_post(db_session, post.id)
    assert result.inserted is True
    rows = (
        (
            await db_session.execute(
                select(ChannelPostEmbedding).where(ChannelPostEmbedding.channel_post_id == post.id),
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].workspace_id == newer_ws.id


@pytest.mark.asyncio
async def test_transient_error_propagates_to_caller(
    db_session: AsyncSession,
    seed: tuple[User, Workspace, Brand, Channel, WorkspaceChannel, ChannelPost],
) -> None:
    """The service doesn't swallow timeouts — the task retries."""

    _u, _ws, _b, _ch, _bind, post = seed

    class FailingProvider:
        async def complete(
            self,
            prompt: str,
            model: str,
            *,
            tools: list[Tool] | None = None,
            max_tokens: int = 2000,
        ) -> LLMResult:  # pragma: no cover - not exercised
            del prompt, model, tools, max_tokens
            return LLMResult(text="")

        async def embed(self, text: str, model: str) -> EmbeddingResult:
            del text, model
            raise LLMTimeoutError("polza timed out")

    provider: LLMProvider = FailingProvider()
    service = EmbeddingsService(provider=provider, model="text-embedding-3-small", dim=4)
    with pytest.raises(LLMTimeoutError):
        await service.embed_channel_post(db_session, post.id)
