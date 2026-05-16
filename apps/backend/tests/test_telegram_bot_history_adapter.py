"""Unit tests for the PR #15 additions to the Telegram Bot adapter.

Covers:

* :class:`MockTelegramBotClient.fetch_channel_history` — newest-first
  ordering, ``from_message_id`` filter, ``limit`` clamp, empty
  return on unknown chat, transport error propagation.
* :class:`MockTelegramBotClient.get_chat_member_count` — happy path,
  not-found and transport-error mapping.
* :class:`AiogramTelegramBotClient.fetch_channel_history` — Bot API
  has no ``getChatHistory``; the implementation is a typed stub
  that returns ``[]`` so the rest of the pipeline still runs.

We don't instantiate :class:`AiogramTelegramBotClient` with a real
token — the stub method is independent of the underlying aiogram
``Bot`` object, so we just call it directly.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.adapters.social import (
    AiogramTelegramBotClient,
    ChannelPostSnapshot,
    MockTelegramBotClient,
    TelegramChannelNotFoundError,
    TelegramTransportError,
)

# ---------------------------------------------------------------------------
# Mock — fetch_channel_history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mock_fetch_channel_history_returns_newest_first() -> None:
    now = datetime.now(tz=UTC)
    mock = MockTelegramBotClient(
        history_by_chat={
            -1001: [
                ChannelPostSnapshot(tg_message_id=1, posted_at=now),
                ChannelPostSnapshot(tg_message_id=3, posted_at=now),
                ChannelPostSnapshot(tg_message_id=2, posted_at=now),
            ]
        }
    )
    result = await mock.fetch_channel_history(-1001, limit=10)
    assert [s.tg_message_id for s in result] == [3, 2, 1]
    assert ("fetch_channel_history", (-1001, 10, None)) in mock.call_log


@pytest.mark.asyncio
async def test_mock_fetch_channel_history_respects_limit() -> None:
    now = datetime.now(tz=UTC)
    mock = MockTelegramBotClient(
        history_by_chat={
            -1001: [ChannelPostSnapshot(tg_message_id=mid, posted_at=now) for mid in range(1, 11)]
        }
    )
    result = await mock.fetch_channel_history(-1001, limit=3)
    assert [s.tg_message_id for s in result] == [10, 9, 8]


@pytest.mark.asyncio
async def test_mock_fetch_channel_history_filters_by_from_message_id() -> None:
    now = datetime.now(tz=UTC)
    mock = MockTelegramBotClient(
        history_by_chat={
            -1001: [
                ChannelPostSnapshot(tg_message_id=mid, posted_at=now) for mid in (10, 20, 30, 40)
            ]
        }
    )
    result = await mock.fetch_channel_history(
        -1001,
        limit=10,
        from_message_id=30,
    )
    # Exclusive upper bound — 30 excluded.
    assert [s.tg_message_id for s in result] == [20, 10]


@pytest.mark.asyncio
async def test_mock_fetch_channel_history_unknown_chat_returns_empty() -> None:
    mock = MockTelegramBotClient()
    result = await mock.fetch_channel_history(-999, limit=5)
    assert result == []


@pytest.mark.asyncio
async def test_mock_fetch_channel_history_raises_transport_error() -> None:
    mock = MockTelegramBotClient(raise_transport_error=True)
    with pytest.raises(TelegramTransportError):
        await mock.fetch_channel_history(-1, limit=1)


# ---------------------------------------------------------------------------
# Mock — get_chat_member_count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mock_get_chat_member_count_returns_seeded_value() -> None:
    mock = MockTelegramBotClient(member_count_by_chat={-1001: 250})
    assert await mock.get_chat_member_count(-1001) == 250
    assert ("get_chat_member_count", (-1001,)) in mock.call_log


@pytest.mark.asyncio
async def test_mock_get_chat_member_count_unknown_chat_raises_not_found() -> None:
    mock = MockTelegramBotClient()
    with pytest.raises(TelegramChannelNotFoundError):
        await mock.get_chat_member_count(-999)


@pytest.mark.asyncio
async def test_mock_get_chat_member_count_transport_error() -> None:
    mock = MockTelegramBotClient(raise_transport_error=True)
    with pytest.raises(TelegramTransportError):
        await mock.get_chat_member_count(-1)


# ---------------------------------------------------------------------------
# Aiogram — fetch_channel_history stub
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aiogram_fetch_channel_history_is_typed_stub() -> None:
    """Bot API has no ``getChatHistory`` — stub returns ``[]``.

    The MTProto user-bot in PR #18 + the live ingest webhook in
    PR #16 are the real sources. PR #15 ships the typed stub so
    the Celery pipeline runs without raising; the service then
    transparently records ``status='no_history'``.
    """

    # ``token`` only matters for outbound calls; the stub never
    # touches the Bot session.
    client = AiogramTelegramBotClient(token="123:fake-token")
    try:
        result = await client.fetch_channel_history(
            -1001234567890,
            limit=100,
            from_message_id=None,
        )
    finally:
        await client.close()
    assert result == []
