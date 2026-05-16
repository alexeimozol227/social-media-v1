"""Thin Telegram Bot API wrapper around ``aiogram 3.x``.

docs/05-tech-stack.md §5.1 + docs/plans/phase1-sprint2-plan.md
(PR #14):

* :class:`TelegramBotClient` — Protocol exposing the three REST
  calls PR #14 needs (``get_chat`` / ``get_chat_member`` /
  ``get_chat_administrators``). PR #16 will extend this with the
  webhook / Dispatcher surface.
* :class:`AiogramTelegramBotClient` — real implementation. Lazily
  imports ``aiogram`` so unit tests using
  :class:`MockTelegramBotClient` don't need the wheel.
* :class:`MockTelegramBotClient` — in-memory fixture used by tests.

The wrapper deliberately surfaces only the data we care about
(channel title / username / description / subscribers count + bot
admin rights) so the service layer doesn't have to know aiogram's
typed-dict shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover - typing only
    from aiogram import Bot


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChannelInfo:
    """Result of :meth:`TelegramBotClient.get_chat`.

    Fields mirror the Bot API ``Chat`` object trimmed down to the
    columns we persist on :class:`app.models.channel.Channel`.
    """

    chat_id: int
    """``Chat.id`` — the canonical TG identifier (negative for channels)."""

    title: str | None
    """``Chat.title`` — display name. ``None`` for direct chats."""

    username: str | None
    """``Chat.username`` — the public ``@handle`` without the ``@`` sign.

    ``None`` for private channels (they only have a numeric id).
    """

    description: str | None
    """``Chat.description`` — long-form channel description."""

    is_public: bool
    """``True`` when ``Chat.username`` is non-empty.

    Mirrors the public/private split. Used by the registry to gate
    user-bot reads (PR #18): we only crawl public channels for
    competitor analytics.
    """

    subscribers_count: int | None = None
    """Optional. Bot API exposes it via ``getChatMemberCount`` which
    we will call from PR #15 (history backfill)."""


@dataclass(frozen=True, slots=True)
class ChatMemberInfo:
    """Subset of ``ChatMember`` we care about for verification.

    ``status`` is the discriminator (``creator`` / ``administrator``
    / ``member`` / ``left`` / ``kicked``). ``can_post_messages`` is
    only meaningful when ``status='administrator'`` (or ``creator``,
    which has every right implicitly).
    """

    user_id: int
    status: str
    can_post_messages: bool = False
    can_edit_messages: bool = False
    can_delete_messages: bool = False


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class TelegramTransportError(Exception):
    """Raised when the Bot API call itself failed (network, 5xx, rate-limit).

    The service layer maps this to
    :class:`app.errors.TelegramAPIError` so we get a typed 502
    response and Sentry tagging.
    """


class TelegramChannelNotFoundError(Exception):
    """Raised when ``get_chat`` returns ``chat not found`` (Bot API 400).

    Distinct from the transport error so the service layer can map
    it to :class:`app.errors.ChannelNotFoundError` (404).
    """


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class TelegramBotClient(Protocol):
    """Public Bot API surface PR #14 needs.

    PR #16 will extend this with ``set_webhook`` / dispatcher
    plumbing; PR #15 adds ``get_chat_member_count`` + a paginated
    history-fetch method.
    """

    async def get_chat(self, identifier: str | int) -> ChannelInfo:
        """Resolve a channel by ``@username`` or numeric ``chat_id``.

        Raises :class:`TelegramChannelNotFoundError` when the channel
        doesn't exist or the bot can't see it,
        :class:`TelegramTransportError` for transport-level failures.
        """

    async def get_chat_member(self, chat_id: int, user_id: int) -> ChatMemberInfo:
        """Read one ``ChatMember``."""

    async def get_chat_administrators(self, chat_id: int) -> list[ChatMemberInfo]:
        """Return every administrator of ``chat_id`` (including the creator)."""

    async def get_me_id(self) -> int:
        """Return the bot's own ``user_id`` (used to look itself up)."""

    async def close(self) -> None:
        """Release any HTTP connection pool the client holds."""


# ---------------------------------------------------------------------------
# Real implementation (aiogram-backed)
# ---------------------------------------------------------------------------


class AiogramTelegramBotClient:
    """Thin wrapper over :class:`aiogram.Bot`.

    Only the synchronous REST surface is exposed; we deliberately
    don't start a Dispatcher / polling loop here. PR #16 will add
    the webhook entry point in a sibling module.
    """

    def __init__(self, token: str) -> None:
        if not token:
            msg = "Telegram bot token must be non-empty."
            raise ValueError(msg)
        # Lazy import so unit tests using ``MockTelegramBotClient``
        # don't have to depend on the aiogram wheel being available.
        from aiogram import Bot

        self._bot: Bot = Bot(token=token)
        self._me_id: int | None = None

    async def get_chat(self, identifier: str | int) -> ChannelInfo:
        from aiogram.exceptions import (
            TelegramBadRequest,
            TelegramNetworkError,
            TelegramRetryAfter,
        )

        try:
            chat = await self._bot.get_chat(identifier)
        except TelegramBadRequest as exc:
            # ``chat not found`` / ``CHAT_NOT_FOUND`` / ``bot was kicked``
            # all surface as ``TelegramBadRequest``; the service layer
            # treats every variant as "not connectable" — 404.
            raise TelegramChannelNotFoundError(str(exc)) from exc
        except (TelegramNetworkError, TelegramRetryAfter) as exc:
            raise TelegramTransportError(str(exc)) from exc
        return ChannelInfo(
            chat_id=chat.id,
            title=chat.title,
            username=chat.username,
            description=getattr(chat, "description", None),
            is_public=bool(chat.username),
        )

    async def get_chat_member(self, chat_id: int, user_id: int) -> ChatMemberInfo:
        from aiogram.exceptions import (
            TelegramBadRequest,
            TelegramNetworkError,
            TelegramRetryAfter,
        )

        try:
            member = await self._bot.get_chat_member(chat_id, user_id)
        except TelegramBadRequest as exc:
            raise TelegramChannelNotFoundError(str(exc)) from exc
        except (TelegramNetworkError, TelegramRetryAfter) as exc:
            raise TelegramTransportError(str(exc)) from exc
        return _member_to_info(member)

    async def get_chat_administrators(self, chat_id: int) -> list[ChatMemberInfo]:
        from aiogram.exceptions import (
            TelegramBadRequest,
            TelegramNetworkError,
            TelegramRetryAfter,
        )

        try:
            admins = await self._bot.get_chat_administrators(chat_id)
        except TelegramBadRequest as exc:
            raise TelegramChannelNotFoundError(str(exc)) from exc
        except (TelegramNetworkError, TelegramRetryAfter) as exc:
            raise TelegramTransportError(str(exc)) from exc
        return [_member_to_info(m) for m in admins]

    async def get_me_id(self) -> int:
        if self._me_id is not None:
            return self._me_id
        me = await self._bot.get_me()
        self._me_id = me.id
        return self._me_id

    async def close(self) -> None:
        await self._bot.session.close()


def _member_to_info(member: object) -> ChatMemberInfo:
    """Translate an aiogram ``ChatMember`` union into our dataclass.

    The aiogram type union is discriminated on ``status``; the
    ``can_post_messages`` / ``can_edit_messages`` /
    ``can_delete_messages`` attributes only exist on the
    ``administrator`` variant, so we use ``getattr`` with sensible
    defaults to flatten the union into a single shape. The
    ``creator`` variant is implicitly fully-permitted in Bot API.
    """

    status = str(getattr(member, "status", ""))
    user_id = int(getattr(getattr(member, "user", None), "id", 0))
    if status == "creator":
        return ChatMemberInfo(
            user_id=user_id,
            status=status,
            can_post_messages=True,
            can_edit_messages=True,
            can_delete_messages=True,
        )
    return ChatMemberInfo(
        user_id=user_id,
        status=status,
        can_post_messages=bool(getattr(member, "can_post_messages", False)),
        can_edit_messages=bool(getattr(member, "can_edit_messages", False)),
        can_delete_messages=bool(getattr(member, "can_delete_messages", False)),
    )


# ---------------------------------------------------------------------------
# Mock implementation (tests + dev without a real bot token)
# ---------------------------------------------------------------------------


@dataclass
class MockTelegramBotClient:
    """In-memory Telegram Bot API double.

    Tests populate :attr:`channels_by_username` /
    :attr:`channels_by_id` to script ``get_chat`` responses, and
    :attr:`members_by_chat` to script ``get_chat_member`` /
    ``get_chat_administrators``. Failure modes are simulated by
    setting :attr:`raise_not_found` (channel resolves to a
    not-found error) or :attr:`raise_transport_error` (transport
    failure on every call).

    The default ``me_id = 42`` makes mocked tests readable; override
    it when you also want to simulate "bot is not even a member".
    """

    channels_by_username: dict[str, ChannelInfo] = field(default_factory=dict)
    channels_by_id: dict[int, ChannelInfo] = field(default_factory=dict)
    members_by_chat: dict[int, list[ChatMemberInfo]] = field(default_factory=dict)
    me_id: int = 42
    raise_not_found: bool = False
    raise_transport_error: bool = False
    call_log: list[tuple[str, tuple[object, ...]]] = field(default_factory=list)

    async def get_chat(self, identifier: str | int) -> ChannelInfo:
        self.call_log.append(("get_chat", (identifier,)))
        if self.raise_transport_error:
            raise TelegramTransportError("mock transport error")
        if self.raise_not_found:
            raise TelegramChannelNotFoundError("mock not found")
        if isinstance(identifier, str):
            key = identifier.removeprefix("@")
            info = self.channels_by_username.get(key)
        else:
            info = self.channels_by_id.get(identifier)
        if info is None:
            raise TelegramChannelNotFoundError(f"channel {identifier!r} not in mock")
        return info

    async def get_chat_member(self, chat_id: int, user_id: int) -> ChatMemberInfo:
        self.call_log.append(("get_chat_member", (chat_id, user_id)))
        if self.raise_transport_error:
            raise TelegramTransportError("mock transport error")
        members = self.members_by_chat.get(chat_id, [])
        for m in members:
            if m.user_id == user_id:
                return m
        # Not found in the admin list → ``status='left'`` mirrors
        # Bot API behavior for a user the bot has never seen.
        return ChatMemberInfo(user_id=user_id, status="left")

    async def get_chat_administrators(self, chat_id: int) -> list[ChatMemberInfo]:
        self.call_log.append(("get_chat_administrators", (chat_id,)))
        if self.raise_transport_error:
            raise TelegramTransportError("mock transport error")
        return list(self.members_by_chat.get(chat_id, []))

    async def get_me_id(self) -> int:
        return self.me_id

    async def close(self) -> None:  # pragma: no cover - no resource
        return None


# ---------------------------------------------------------------------------
# Module-level provider
# ---------------------------------------------------------------------------

# Singleton client + override hook used by tests. The provider
# returns the production client when ``settings.telegram_bot_token``
# is non-empty; otherwise a mock so dev/CI runs end-to-end without a
# real ``@BotFather`` token.
_client: TelegramBotClient | None = None
_override: TelegramBotClient | None = None


def set_telegram_bot_client_override(client: TelegramBotClient | None) -> None:
    """Pin a specific client (tests). Pass ``None`` to clear."""

    global _override
    _override = client


def get_telegram_bot_client() -> TelegramBotClient:
    """Return the process-wide :class:`TelegramBotClient` singleton.

    Honors :func:`set_telegram_bot_client_override` so tests can
    swap in a :class:`MockTelegramBotClient` without monkey-patching
    every callsite.
    """

    global _client
    if _override is not None:
        return _override
    if _client is not None:
        return _client

    from app.core.config import settings

    token = settings.telegram_bot_token or settings.telegram_bot_token_dev
    _client = AiogramTelegramBotClient(token=token) if token else MockTelegramBotClient()
    return _client


__all__ = [
    "AiogramTelegramBotClient",
    "ChannelInfo",
    "ChatMemberInfo",
    "MockTelegramBotClient",
    "TelegramBotClient",
    "TelegramChannelNotFoundError",
    "TelegramTransportError",
    "get_telegram_bot_client",
    "set_telegram_bot_client_override",
]
