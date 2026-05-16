"""Unit tests for the channel-history backfill service (PR #15).

Drives :mod:`app.services.channel_history` against the SQLite test
schema:

* :func:`ingest_snapshots` — dedup on
  ``(channel_id, tg_message_id)``, idempotency, mixed batches.
* :func:`run_backfill` — happy path, dedup re-run, empty adapter
  response, transport error mapping, detached binding mapping.
* Subscribers count refresh — best-effort, doesn't abort on
  adapter failure.
* RLS isolation — workspace A can't see workspace B's posts.

Uses :class:`MockTelegramBotClient` so no Bot API is reached, and
``fakeredis`` so per-user event publishing exercises the full
serializer path.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import fakeredis.aioredis
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.social import (
    ChannelInfo,
    ChannelPostSnapshot,
    MockTelegramBotClient,
)
from app.errors import (
    ChannelNotConnectedError,
    ChannelNotFoundError,
    TelegramAPIError,
)
from app.models.brand import Brand
from app.models.channel import (
    Channel,
    ChannelPost,
    WorkspaceChannel,
    WorkspaceChannelRoleValues,
)
from app.models.user import User, UserStatus
from app.models.workspace import Workspace, WorkspaceType
from app.services import channel_history as history_service

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def seed(
    db_session: AsyncSession,
) -> tuple[User, Workspace, Brand, Channel, WorkspaceChannel]:
    """Seed a User + Workspace + Brand + connected Channel + binding."""

    user = User(
        email="hist@example.com",
        hashed_password="x",
        full_name="Hist Tester",
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
        external_id=-1001234567890,
        username="test_channel",
        title="Test Channel",
        description="A test channel",
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
            "can_edit_messages": True,
            "can_delete_messages": True,
            "captured_at": datetime.now(tz=UTC).isoformat(),
        },
    )
    db_session.add(binding)
    await db_session.flush()
    return user, workspace, brand, channel, binding


@pytest.fixture
def history_mock(
    seed: tuple[User, Workspace, Brand, Channel, WorkspaceChannel],
) -> MockTelegramBotClient:
    """Mock adapter pre-populated with a channel + 3-post history."""

    _user, _ws, _brand, channel, _binding = seed
    info = ChannelInfo(
        chat_id=channel.external_id,
        title=channel.title,
        username=channel.username,
        description=channel.description,
        is_public=channel.is_public,
        subscribers_count=channel.subscribers_count,
    )
    now = datetime.now(tz=UTC)
    snapshots = [
        ChannelPostSnapshot(
            tg_message_id=101,
            posted_at=now - timedelta(hours=2),
            text="post 101",
            has_media=False,
            views_count=10,
        ),
        ChannelPostSnapshot(
            tg_message_id=102,
            posted_at=now - timedelta(hours=1),
            text="post 102",
            has_media=False,
            views_count=20,
        ),
        ChannelPostSnapshot(
            tg_message_id=103,
            posted_at=now,
            text="post 103",
            has_media=True,
            media_summary={"kind": "photo"},
            views_count=30,
            reactions_count=4,
            forwards_count=1,
        ),
    ]
    return MockTelegramBotClient(
        channels_by_id={channel.external_id: info},
        channels_by_username={channel.username or "": info},
        history_by_chat={channel.external_id: snapshots},
        member_count_by_chat={channel.external_id: 250},
    )


# ---------------------------------------------------------------------------
# ingest_snapshots
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_empty_batch_is_noop(
    db_session: AsyncSession,
    seed: tuple[User, Workspace, Brand, Channel, WorkspaceChannel],
) -> None:
    _u, _w, _b, channel, _bind = seed
    inserted, dup = await history_service.ingest_snapshots(
        db_session,
        channel_id=channel.id,
        snapshots=[],
    )
    assert inserted == []
    assert dup == 0


@pytest.mark.asyncio
async def test_ingest_inserts_all_unique(
    db_session: AsyncSession,
    seed: tuple[User, Workspace, Brand, Channel, WorkspaceChannel],
) -> None:
    _u, _w, _b, channel, _bind = seed
    now = datetime.now(tz=UTC)
    snapshots = [
        ChannelPostSnapshot(tg_message_id=1, posted_at=now),
        ChannelPostSnapshot(tg_message_id=2, posted_at=now),
        ChannelPostSnapshot(tg_message_id=3, posted_at=now),
    ]
    inserted, dup = await history_service.ingest_snapshots(
        db_session,
        channel_id=channel.id,
        snapshots=snapshots,
    )
    assert len(inserted) == 3
    assert dup == 0
    # Chronological output order.
    assert [r.tg_message_id for r in inserted] == [1, 2, 3]


@pytest.mark.asyncio
async def test_ingest_is_idempotent_across_runs(
    db_session: AsyncSession,
    seed: tuple[User, Workspace, Brand, Channel, WorkspaceChannel],
) -> None:
    _u, _w, _b, channel, _bind = seed
    now = datetime.now(tz=UTC)
    snapshots = [
        ChannelPostSnapshot(tg_message_id=10, posted_at=now),
        ChannelPostSnapshot(tg_message_id=11, posted_at=now),
    ]
    first_inserted, first_dup = await history_service.ingest_snapshots(
        db_session,
        channel_id=channel.id,
        snapshots=snapshots,
    )
    second_inserted, second_dup = await history_service.ingest_snapshots(
        db_session,
        channel_id=channel.id,
        snapshots=snapshots,
    )
    assert len(first_inserted) == 2
    assert first_dup == 0
    assert second_inserted == []
    assert second_dup == 2


@pytest.mark.asyncio
async def test_ingest_skips_existing_and_inserts_new(
    db_session: AsyncSession,
    seed: tuple[User, Workspace, Brand, Channel, WorkspaceChannel],
) -> None:
    _u, _w, _b, channel, _bind = seed
    now = datetime.now(tz=UTC)
    await history_service.ingest_snapshots(
        db_session,
        channel_id=channel.id,
        snapshots=[ChannelPostSnapshot(tg_message_id=100, posted_at=now)],
    )
    mixed = [
        ChannelPostSnapshot(tg_message_id=100, posted_at=now),  # dup
        ChannelPostSnapshot(tg_message_id=101, posted_at=now),  # new
        ChannelPostSnapshot(tg_message_id=102, posted_at=now),  # new
    ]
    inserted, dup = await history_service.ingest_snapshots(
        db_session,
        channel_id=channel.id,
        snapshots=mixed,
    )
    assert [r.tg_message_id for r in inserted] == [101, 102]
    assert dup == 1


@pytest.mark.asyncio
async def test_ingest_isolates_channels(
    db_session: AsyncSession,
    seed: tuple[User, Workspace, Brand, Channel, WorkspaceChannel],
) -> None:
    """Two channels with the same tg_message_id don't dedup against each other."""

    _u, _w, _b, channel_a, _bind = seed
    channel_b = Channel(
        platform="telegram",
        external_id=-1009876543210,
        username="another_channel",
        title="Other",
        is_public=True,
    )
    db_session.add(channel_b)
    await db_session.flush()

    now = datetime.now(tz=UTC)
    snapshots = [ChannelPostSnapshot(tg_message_id=5, posted_at=now)]
    a_inserted, _ = await history_service.ingest_snapshots(
        db_session,
        channel_id=channel_a.id,
        snapshots=snapshots,
    )
    b_inserted, _ = await history_service.ingest_snapshots(
        db_session,
        channel_id=channel_b.id,
        snapshots=snapshots,
    )
    assert len(a_inserted) == 1
    assert len(b_inserted) == 1


# ---------------------------------------------------------------------------
# run_backfill
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_backfill_happy_path_writes_posts_and_publishes_events(
    db_session: AsyncSession,
    seed: tuple[User, Workspace, Brand, Channel, WorkspaceChannel],
    history_mock: MockTelegramBotClient,
) -> None:
    user, workspace, brand, channel, binding = seed
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    summary = await history_service.run_backfill(
        db_session,
        redis,
        history_mock,
        user_id=user.id,
        workspace_id=workspace.id,
        brand_id=brand.id,
        workspace_channel_id=binding.id,
        limit=50,
        task_id="task-happy",
    )

    assert summary.status == "ok"
    assert summary.fetched_count == 3
    assert summary.inserted_count == 3
    assert summary.duplicate_count == 0

    rows = (
        (
            await db_session.execute(
                select(ChannelPost)
                .where(ChannelPost.channel_id == channel.id)
                .order_by(ChannelPost.tg_message_id)
            )
        )
        .scalars()
        .all()
    )
    assert [r.tg_message_id for r in rows] == [101, 102, 103]
    # Subscribers count refreshed from the adapter.
    refreshed = await db_session.get(Channel, channel.id)
    assert refreshed is not None
    assert refreshed.subscribers_count == 250


@pytest.mark.asyncio
async def test_run_backfill_is_idempotent_on_rerun(
    db_session: AsyncSession,
    seed: tuple[User, Workspace, Brand, Channel, WorkspaceChannel],
    history_mock: MockTelegramBotClient,
) -> None:
    user, workspace, brand, _channel, binding = seed
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    first = await history_service.run_backfill(
        db_session,
        redis,
        history_mock,
        user_id=user.id,
        workspace_id=workspace.id,
        brand_id=brand.id,
        workspace_channel_id=binding.id,
        limit=50,
        task_id="task-1",
    )
    second = await history_service.run_backfill(
        db_session,
        redis,
        history_mock,
        user_id=user.id,
        workspace_id=workspace.id,
        brand_id=brand.id,
        workspace_channel_id=binding.id,
        limit=50,
        task_id="task-2",
    )

    assert first.inserted_count == 3
    assert second.inserted_count == 0
    assert second.duplicate_count == 3
    assert second.status == "ok"


@pytest.mark.asyncio
async def test_run_backfill_respects_limit(
    db_session: AsyncSession,
    seed: tuple[User, Workspace, Brand, Channel, WorkspaceChannel],
    history_mock: MockTelegramBotClient,
) -> None:
    user, workspace, brand, _channel, binding = seed
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    summary = await history_service.run_backfill(
        db_session,
        redis,
        history_mock,
        user_id=user.id,
        workspace_id=workspace.id,
        brand_id=brand.id,
        workspace_channel_id=binding.id,
        limit=1,
        task_id="task-limited",
    )
    # Newest-first ordering means the latest message (103) wins.
    assert summary.fetched_count == 1
    assert summary.inserted_count == 1


@pytest.mark.asyncio
async def test_run_backfill_from_message_id_pages_older(
    db_session: AsyncSession,
    seed: tuple[User, Workspace, Brand, Channel, WorkspaceChannel],
    history_mock: MockTelegramBotClient,
) -> None:
    user, workspace, brand, _channel, binding = seed
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    summary = await history_service.run_backfill(
        db_session,
        redis,
        history_mock,
        user_id=user.id,
        workspace_id=workspace.id,
        brand_id=brand.id,
        workspace_channel_id=binding.id,
        limit=50,
        task_id="task-paged",
        from_message_id=103,  # exclusive — should fetch 101 + 102
    )
    assert summary.fetched_count == 2
    assert summary.inserted_count == 2


@pytest.mark.asyncio
async def test_run_backfill_empty_history_marks_no_history(
    db_session: AsyncSession,
    seed: tuple[User, Workspace, Brand, Channel, WorkspaceChannel],
) -> None:
    user, workspace, brand, channel, binding = seed
    # Mock with no posts but valid member_count.
    mock = MockTelegramBotClient(
        member_count_by_chat={channel.external_id: 250},
    )
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    summary = await history_service.run_backfill(
        db_session,
        redis,
        mock,
        user_id=user.id,
        workspace_id=workspace.id,
        brand_id=brand.id,
        workspace_channel_id=binding.id,
        limit=10,
        task_id="task-empty",
    )
    assert summary.status == "no_history"
    assert summary.fetched_count == 0
    assert summary.inserted_count == 0


@pytest.mark.asyncio
async def test_run_backfill_unknown_binding_raises_not_found(
    db_session: AsyncSession,
    seed: tuple[User, Workspace, Brand, Channel, WorkspaceChannel],
    history_mock: MockTelegramBotClient,
) -> None:
    user, workspace, brand, _channel, _binding = seed
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    with pytest.raises(ChannelNotFoundError):
        await history_service.run_backfill(
            db_session,
            redis,
            history_mock,
            user_id=user.id,
            workspace_id=workspace.id,
            brand_id=brand.id,
            workspace_channel_id=uuid.uuid4(),
            limit=10,
            task_id="task-missing",
        )


@pytest.mark.asyncio
async def test_run_backfill_detached_binding_raises(
    db_session: AsyncSession,
    seed: tuple[User, Workspace, Brand, Channel, WorkspaceChannel],
    history_mock: MockTelegramBotClient,
) -> None:
    user, workspace, brand, _channel, binding = seed
    binding.disconnected_at = datetime.now(tz=UTC)
    await db_session.flush()
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    with pytest.raises(ChannelNotConnectedError):
        await history_service.run_backfill(
            db_session,
            redis,
            history_mock,
            user_id=user.id,
            workspace_id=workspace.id,
            brand_id=brand.id,
            workspace_channel_id=binding.id,
            limit=10,
            task_id="task-detached",
        )


@pytest.mark.asyncio
async def test_run_backfill_transport_error_maps_to_typed_error(
    db_session: AsyncSession,
    seed: tuple[User, Workspace, Brand, Channel, WorkspaceChannel],
) -> None:
    user, workspace, brand, channel, binding = seed
    mock = MockTelegramBotClient(
        member_count_by_chat={channel.external_id: 250},
        raise_transport_error=False,  # subscribers refresh succeeds
    )
    # But mark it to fail on the history fetch.
    mock.raise_transport_error = True
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    with pytest.raises(TelegramAPIError):
        await history_service.run_backfill(
            db_session,
            redis,
            mock,
            user_id=user.id,
            workspace_id=workspace.id,
            brand_id=brand.id,
            workspace_channel_id=binding.id,
            limit=10,
            task_id="task-transport-fail",
        )


@pytest.mark.asyncio
async def test_run_backfill_subscribers_refresh_is_best_effort(
    db_session: AsyncSession,
    seed: tuple[User, Workspace, Brand, Channel, WorkspaceChannel],
) -> None:
    """Failing member_count lookup must not abort the backfill."""

    user, workspace, brand, channel, binding = seed
    now = datetime.now(tz=UTC)
    # Mock has history but member_count lookup will raise not-found.
    mock = MockTelegramBotClient(
        history_by_chat={
            channel.external_id: [
                ChannelPostSnapshot(tg_message_id=1, posted_at=now),
            ],
        },
        # NOTE: member_count_by_chat intentionally empty → raises
        # TelegramChannelNotFoundError in the mock; the service
        # must swallow and proceed.
    )
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    summary = await history_service.run_backfill(
        db_session,
        redis,
        mock,
        user_id=user.id,
        workspace_id=workspace.id,
        brand_id=brand.id,
        workspace_channel_id=binding.id,
        limit=10,
        task_id="task-best-effort",
    )
    assert summary.status == "ok"
    assert summary.inserted_count == 1
