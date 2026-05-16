"""Tests for :class:`app.adapters.userbot.MockUserBotClient`."""

from __future__ import annotations

import pytest

from app.adapters.userbot import (
    MockUserBotClient,
    UserBotChannelInfo,
    UserBotChannelNotFoundError,
)


@pytest.mark.asyncio
async def test_fetch_chat_info_default_fixture() -> None:
    mock = MockUserBotClient()
    info = await mock.fetch_chat_info("@mock_public_channel")
    assert isinstance(info, UserBotChannelInfo)
    assert info.username == "mock_public_channel"
    assert info.is_public is True
    assert info.chat_id == -1001234567890


@pytest.mark.asyncio
async def test_fetch_chat_info_unknown_raises() -> None:
    mock = MockUserBotClient()
    with pytest.raises(UserBotChannelNotFoundError):
        await mock.fetch_chat_info("@ghost")


@pytest.mark.asyncio
async def test_fetch_chat_info_strip_at_and_case() -> None:
    """Mock normalises ``@USERNAME`` → ``username``."""

    info = UserBotChannelInfo(
        chat_id=-100123,
        title="Spam Inc",
        username="spam_inc",
        description=None,
        is_public=True,
    )
    mock = MockUserBotClient(channels={"spam_inc": info})
    out = await mock.fetch_chat_info("@SPAM_INC")
    assert out.chat_id == -100123


@pytest.mark.asyncio
async def test_fetch_channel_history_default_fixture() -> None:
    mock = MockUserBotClient()
    posts = await mock.fetch_channel_history(-1001234567890, limit=10)
    assert len(posts) == 2
    # Newest-first.
    assert posts[0].tg_message_id == 102
    assert posts[1].tg_message_id == 101


@pytest.mark.asyncio
async def test_fetch_channel_history_respects_from_message_id() -> None:
    mock = MockUserBotClient()
    posts = await mock.fetch_channel_history(
        -1001234567890,
        limit=10,
        from_message_id=102,
    )
    assert len(posts) == 1
    assert posts[0].tg_message_id == 101


@pytest.mark.asyncio
async def test_healthcheck_default_ok() -> None:
    mock = MockUserBotClient()
    assert await mock.healthcheck() is True
    assert mock.calls["healthcheck"] == 1


@pytest.mark.asyncio
async def test_healthcheck_can_return_false() -> None:
    mock = MockUserBotClient(healthcheck_result=False)
    assert await mock.healthcheck() is False
