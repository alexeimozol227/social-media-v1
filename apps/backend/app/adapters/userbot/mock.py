"""In-memory mock :class:`UserBotClient` for tests + dev (PR #18).

Mirrors :class:`app.adapters.social.telegram_bot.MockTelegramBotClient`
and :class:`app.adapters.llm.mock.MockLLMProvider`: hand-crafted
fixtures keyed by identifier, deterministic across runs, no network.

Fixture override is the contract: tests construct
:class:`MockUserBotClient` with ``channels=...`` / ``histories=...``
keyword arguments, override the default identifier with their own,
and the assert against the resulting :class:`ChannelPostSnapshot`
list. The defaults exist so a plain ``MockUserBotClient()`` returns
a sensible "happy path" for the pool / healthcheck tests that don't
care about specific content.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.adapters.social.telegram_bot import ChannelPostSnapshot
from app.adapters.userbot.base import (
    UserBotChannelInfo,
    UserBotChannelNotFoundError,
    UserBotChannelPrivateError,
)

_DEFAULT_CHANNEL_INFO = UserBotChannelInfo(
    chat_id=-1001234567890,
    title="Mock Public Channel",
    username="mock_public_channel",
    description="A deterministic fixture for unit tests.",
    is_public=True,
    participants_count=12345,
)

_DEFAULT_HISTORY: tuple[ChannelPostSnapshot, ...] = (
    ChannelPostSnapshot(
        tg_message_id=101,
        posted_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        text="First fixture post",
        has_media=False,
        views_count=1000,
    ),
    ChannelPostSnapshot(
        tg_message_id=102,
        posted_at=datetime(2026, 1, 2, 12, 0, tzinfo=UTC),
        text="Second fixture post with media",
        has_media=True,
        media_summary={"kind": "photo"},
        views_count=2000,
        reactions_count=50,
    ),
)


class MockUserBotClient:
    """In-memory implementation of the :class:`UserBotClient` protocol.

    Optional ``channels`` / ``histories`` override the defaults:

    * ``channels`` maps identifier (``@username`` without ``@``) →
      :class:`UserBotChannelInfo`. Use it to test the "private chat"
      branch by setting ``is_public=False``.
    * ``histories`` maps ``chat_id`` (numeric) → list of
      :class:`ChannelPostSnapshot`. Defaults to the two-post fixture.
    * ``healthcheck_result`` flips :meth:`healthcheck` between ok /
      not ok for retry-policy tests.
    """

    def __init__(
        self,
        *,
        channels: dict[str, UserBotChannelInfo] | None = None,
        histories: dict[int, list[ChannelPostSnapshot]] | None = None,
        healthcheck_result: bool = True,
    ) -> None:
        self._channels: dict[str, UserBotChannelInfo] = {
            "mock_public_channel": _DEFAULT_CHANNEL_INFO,
        }
        if channels:
            self._channels.update(channels)
        self._histories: dict[int, list[ChannelPostSnapshot]] = {
            -1001234567890: list(_DEFAULT_HISTORY),
        }
        if histories:
            self._histories.update(histories)
        self._healthcheck_result = healthcheck_result
        self._closed = False

        # Track call counts so tests can assert on usage without
        # monkeypatching internals.
        self.calls: dict[str, int] = {
            "fetch_chat_info": 0,
            "fetch_channel_history": 0,
            "healthcheck": 0,
            "close": 0,
        }

    async def fetch_chat_info(self, identifier: str) -> UserBotChannelInfo:
        self.calls["fetch_chat_info"] += 1
        normalised = identifier.strip().lstrip("@").lower()
        info = self._channels.get(normalised)
        if info is None:
            raise UserBotChannelNotFoundError(
                f"Unknown public channel @{normalised} in MockUserBotClient",
            )
        return info

    async def fetch_channel_history(
        self,
        chat_id: int,
        *,
        limit: int,
        from_message_id: int | None = None,
    ) -> list[ChannelPostSnapshot]:
        self.calls["fetch_channel_history"] += 1
        history = self._histories.get(chat_id)
        if history is None:
            raise UserBotChannelNotFoundError(
                f"Unknown chat_id {chat_id} in MockUserBotClient",
            )
        filtered = history
        if from_message_id is not None:
            filtered = [post for post in filtered if post.tg_message_id < from_message_id]
        # Newest-first, like Pyrogram.
        filtered = sorted(filtered, key=lambda p: p.tg_message_id, reverse=True)
        return filtered[:limit]

    async def healthcheck(self) -> bool:
        self.calls["healthcheck"] += 1
        return self._healthcheck_result

    async def close(self) -> None:
        self.calls["close"] += 1
        self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed

    def add_channel(self, identifier: str, info: UserBotChannelInfo) -> None:
        """Test helper to register an extra channel after construction."""

        self._channels[identifier.strip().lstrip("@").lower()] = info

    def raise_private(self, identifier: str) -> None:
        """Mark ``identifier`` as a private channel; ``fetch_chat_info``
        will continue to return the info, but tests can flip the
        ``is_public`` flag and rely on the competitor service to
        reject the connect attempt with ``COMPETITOR_NOT_PUBLIC``.
        """

        normalised = identifier.strip().lstrip("@").lower()
        existing = self._channels.get(normalised)
        if existing is None:
            raise UserBotChannelPrivateError(
                f"Unknown channel @{normalised} in MockUserBotClient",
            )
        # Replace with is_public=False projection.
        self._channels[normalised] = UserBotChannelInfo(
            chat_id=existing.chat_id,
            title=existing.title,
            username=None,
            description=existing.description,
            is_public=False,
            participants_count=existing.participants_count,
        )

    # The Protocol doesn't require ``__repr__`` but it helps debug
    # pytest diffs when two mock clients are compared.
    def __repr__(self) -> str:
        return (
            f"MockUserBotClient(channels={list(self._channels)!r}, "
            f"histories={list(self._histories)!r}, "
            f"healthcheck_result={self._healthcheck_result!r})"
        )

    def add_history(
        self,
        chat_id: int,
        posts: list[ChannelPostSnapshot] | tuple[ChannelPostSnapshot, ...],
    ) -> None:
        """Test helper to register a history fixture for ``chat_id``."""

        self._histories[chat_id] = list(posts)


# Sentinel for tests that need to assert "no client built yet".
_NONE_FIXTURE: dict[str, Any] = {}


__all__ = [
    "MockUserBotClient",
]
