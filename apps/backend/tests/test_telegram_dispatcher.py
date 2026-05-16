"""Unit tests for :mod:`app.integrations.telegram.dispatcher` (PR #16).

The dispatcher routes raw aiogram ``Update`` objects to the
``ingest`` callable passed at build time. We feed it three kinds of
updates and assert the callable is invoked with the right
``edited=...`` flag (or not at all).

We use a real aiogram :class:`Dispatcher` (no mocking of aiogram) so
the test exercises the actual router registration logic.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from aiogram import Bot
from aiogram.types import Message, Update

from app.integrations.telegram.dispatcher import build_dispatcher


class _IngestSpy:
    """Records every call so tests can assert routing."""

    def __init__(self) -> None:
        self.calls: list[tuple[Message, bool]] = []

    async def __call__(self, message: Message, *, edited: bool) -> None:
        self.calls.append((message, edited))


def _chat() -> dict[str, Any]:
    return {
        "id": -1001234567890,
        "type": "channel",
        "title": "Test Channel",
        "username": "test_channel",
    }


def _channel_post_update(*, edited: bool, message_id: int = 1) -> Update:
    msg: dict[str, Any] = {
        "message_id": message_id,
        "date": 1_700_000_000,
        "chat": _chat(),
        "text": "hello",
    }
    payload: dict[str, Any] = {"update_id": 42}
    if edited:
        msg["edit_date"] = 1_700_000_100
        payload["edited_channel_post"] = msg
    else:
        payload["channel_post"] = msg
    return Update.model_validate(payload)


class _DummyBot:
    """Minimal stand-in for :class:`aiogram.Bot`.

    The channel-ingest handlers never call out to the Bot API, so a
    duck-typed shim is enough \u2014 we keep it as a separate class
    (rather than instantiating a real :class:`aiogram.Bot`) to avoid
    needing a token + httpx session at test time.
    """

    @property
    def id(self) -> int:
        return 0


def _dummy_bot() -> Bot:
    return cast(Bot, _DummyBot())


@pytest.mark.asyncio
async def test_dispatcher_routes_channel_post() -> None:
    spy = _IngestSpy()
    dispatcher = build_dispatcher(spy)

    await dispatcher.feed_webhook_update(
        _dummy_bot(),
        _channel_post_update(edited=False, message_id=10),
    )

    assert len(spy.calls) == 1
    message, edited = spy.calls[0]
    assert message.message_id == 10
    assert edited is False


@pytest.mark.asyncio
async def test_dispatcher_routes_edited_channel_post() -> None:
    spy = _IngestSpy()
    dispatcher = build_dispatcher(spy)

    await dispatcher.feed_webhook_update(
        _dummy_bot(),
        _channel_post_update(edited=True, message_id=11),
    )

    assert len(spy.calls) == 1
    message, edited = spy.calls[0]
    assert message.message_id == 11
    assert edited is True


@pytest.mark.asyncio
async def test_dispatcher_ignores_private_message() -> None:
    """Updates the bot would receive for DMs (``message`` field) must
    not trigger the channel-ingest pipeline."""

    spy = _IngestSpy()
    dispatcher = build_dispatcher(spy)

    private_update = Update.model_validate(
        {
            "update_id": 100,
            "message": {
                "message_id": 1,
                "date": 1_700_000_000,
                "chat": {"id": 1, "type": "private", "first_name": "Alice"},
                "from": {"id": 1, "is_bot": False, "first_name": "Alice"},
                "text": "/start",
            },
        }
    )
    await dispatcher.feed_webhook_update(_dummy_bot(), private_update)
    assert spy.calls == []


@pytest.mark.asyncio
async def test_dispatcher_ignores_empty_update() -> None:
    spy = _IngestSpy()
    dispatcher = build_dispatcher(spy)

    empty_update = Update.model_validate({"update_id": 200})
    await dispatcher.feed_webhook_update(_dummy_bot(), empty_update)
    assert spy.calls == []
