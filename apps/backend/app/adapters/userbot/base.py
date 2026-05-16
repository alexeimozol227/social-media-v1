"""User-bot adapter Protocol + error hierarchy (PR #18).

docs/05-tech-stack.md §5.2 (D40): the user-bot is the MTProto-level
read-only crawler that complements the Bot API for things the Bot
API can't do — history of arbitrary public channels (no admin
membership), member counts on channels we don't run, etc.

PR #18 ships only the contracts:

* :class:`UserBotClient` — Protocol covering ``fetch_chat_info`` /
  ``fetch_channel_history`` / ``healthcheck``.
* :class:`UserBotChannelInfo` — adapter-facing projection of public
  chat metadata; mirrors the columns we persist on
  :class:`app.models.channel.Channel`.
* Typed errors so the service / pool / Celery task layers map them
  to stable application error codes without sniffing exception text.

The actual MTProto wiring (Pyrogram) lives in :mod:`pyrogram_client`
and remains a skeleton until Sprint 3 — see :class:`PyrogramUserBotClient`.
The mock adapter in :mod:`mock` returns deterministic fixtures so
unit tests can exercise the pool / healthcheck / competitor flows
end-to-end without a real Telegram session.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.adapters.social.telegram_bot import ChannelPostSnapshot

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UserBotChannelInfo:
    """Public-chat metadata projection returned by the user-bot.

    Mirrors :class:`app.adapters.social.telegram_bot.ChannelInfo`
    but adds ``participants_count`` because Pyrogram's ``get_chat``
    surfaces it inline (whereas the Bot API requires a separate
    ``getChatMemberCount`` round-trip).
    """

    chat_id: int
    """``Chat.id`` — canonical TG identifier (negative for channels)."""

    title: str | None
    """``Chat.title`` — display name. ``None`` only for direct chats
    (which we never crawl with the user-bot)."""

    username: str | None
    """``Chat.username`` — public ``@handle`` without the leading ``@``.

    ``None`` for private channels. The competitor-connect flow rejects
    private channels because the user-bot path is meant for public
    crawling only — see :func:`app.services.competitors.connect_competitor`.
    """

    description: str | None
    """``Chat.about`` (Pyrogram) / ``Chat.description`` (Bot API)
    — channel "about" text. May be ``None`` for empty descriptions."""

    is_public: bool
    """``True`` iff ``username`` is non-empty. Materialised so the
    service layer doesn't have to repeat the ``if username``
    check at every call site."""

    participants_count: int | None = None
    """Subscriber count. Exposed by Pyrogram inline on ``get_chat``;
    the Bot API requires a separate call so this is ``None`` on the
    Bot API path."""


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class UserBotError(Exception):
    """Base class for user-bot adapter errors."""


class UserBotChannelNotFoundError(UserBotError):
    """Raised by :meth:`UserBotClient.fetch_chat_info` when the channel
    doesn't exist or the user-bot account can't see it."""


class UserBotChannelPrivateError(UserBotError):
    """Raised when ``fetch_channel_history`` is called on a private
    channel (the user-bot pool only crawls public ``@usernames``)."""


class UserBotFloodWaitError(UserBotError):
    """Raised when Telegram returns ``FloodWait`` — the session has
    hit the per-IP rate limit and must cool down for ``retry_after``
    seconds. The pool consumer reads :attr:`retry_after` and calls
    :meth:`UserBotPool.mark_flood_wait`.
    """

    def __init__(self, message: str, *, retry_after: int) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class UserBotAuthError(UserBotError):
    """Raised when the session string is no longer valid — either the
    user revoked it from Telegram's "Active sessions" UI, the account
    was banned, or 2FA was rotated. The pool consumer treats this as
    a permanent failure (:meth:`UserBotPool.mark_banned`) and does
    not retry.
    """


class UserBotTransportError(UserBotError):
    """Raised on transient network / DC failures. The pool consumer
    retries once with a short backoff before giving up."""


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class UserBotClient(Protocol):
    """MTProto read-only surface for the platform user-bot pool.

    All methods are ``async`` so the worker can interleave many
    sessions on a single event loop. Implementations are stateful
    (Pyrogram holds a long-lived connection) but the pool tears the
    client down after each invocation when running on the mock
    adapter, and re-uses Pyrogram clients across calls in production
    (Sprint 3).
    """

    async def fetch_chat_info(self, identifier: str) -> UserBotChannelInfo:
        """Resolve public-chat metadata by ``@username``.

        Raises :class:`UserBotChannelNotFoundError` when the chat
        doesn't exist or is unreachable, :class:`UserBotChannelPrivateError`
        for non-public chats, :class:`UserBotFloodWaitError` /
        :class:`UserBotTransportError` for rate-limit / network issues.
        """

    async def fetch_channel_history(
        self,
        chat_id: int,
        *,
        limit: int,
        from_message_id: int | None = None,
    ) -> list[ChannelPostSnapshot]:
        """Fetch up to ``limit`` posts from ``chat_id`` newest-first.

        ``from_message_id`` is the exclusive upper bound — pass
        ``None`` to start from the most-recent post. Implementations
        MAY return fewer than ``limit`` snapshots if the channel is
        shorter than the window or some messages are unreachable.
        Same error contract as :meth:`fetch_chat_info`.
        """

    async def healthcheck(self) -> bool:
        """Cheap liveness check (``getMe`` on the bound session).

        Returns ``True`` if the session is still authenticated and
        the connection is alive, ``False`` for soft failures (auth
        revoked, account flagged). Raises
        :class:`UserBotTransportError` for transient network errors
        — the Celery healthcheck task converts that into a retry.
        """

    async def close(self) -> None:
        """Release any underlying MTProto connection."""


__all__ = [
    "UserBotAuthError",
    "UserBotChannelInfo",
    "UserBotChannelNotFoundError",
    "UserBotChannelPrivateError",
    "UserBotClient",
    "UserBotError",
    "UserBotFloodWaitError",
    "UserBotTransportError",
]
