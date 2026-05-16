"""Thin Telegram Bot API wrapper around ``aiogram 3.x``.

docs/05-tech-stack.md §5.1 + docs/plans/phase1-sprint2-plan.md
(PR #14 + PR #15):

* :class:`TelegramBotClient` — Protocol exposing the REST calls
  the service layer needs: ``get_chat`` / ``get_chat_member`` /
  ``get_chat_administrators`` (PR #14) plus
  ``get_chat_member_count`` / ``fetch_channel_history`` for the
  history backfill (PR #15). PR #16 extends the protocol with the
  webhook / Dispatcher surface.
* :class:`AiogramTelegramBotClient` — real implementation. Lazily
  imports ``aiogram`` so unit tests using
  :class:`MockTelegramBotClient` don't need the wheel.
* :class:`MockTelegramBotClient` — in-memory fixture used by tests.

The wrapper deliberately surfaces only the data we care about
(channel title / username / description / subscribers count + bot
admin rights + post snapshots) so the service layer doesn't have
to know aiogram's typed-dict shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

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


@dataclass(frozen=True, slots=True)
class WebhookInfo:
    """Result of :meth:`TelegramBotClient.get_webhook_info` (PR #16).

    Mirrors the Bot API ``WebhookInfo`` object trimmed down to the
    fields we display in the admin panel / use for health checks.
    The full upstream object also exposes ``last_error_date`` and
    ``last_error_message`` for debugging; we surface them so a
    delivery storm shows up in observability without an extra round
    trip.
    """

    url: str
    """Currently-configured webhook URL. Empty string when no webhook
    is set (the bot is in long-polling mode)."""

    has_custom_certificate: bool = False
    """True when the bot was registered with a self-signed cert
    (we use Telegram's public TLS chain, so this is always False
    in production)."""

    pending_update_count: int = 0
    """Updates queued on the TG side. A non-zero value after a fresh
    ``set_webhook`` call means TG is still draining the previous
    long-polling backlog."""

    last_error_date: datetime | None = None
    """UTC timestamp of the last delivery failure, if any."""

    last_error_message: str | None = None
    """Free-form error string from Telegram; surfaced as-is."""

    allowed_updates: tuple[str, ...] = ()
    """Update kinds Telegram will forward — we always pin this to
    ``("channel_post", "edited_channel_post")`` for the channel ingest
    pipeline."""


@dataclass(frozen=True, slots=True)
class ChannelPostSnapshot:
    """Adapter-facing projection of one channel post (PR #15).

    Mirrors the columns we persist on :class:`app.models.channel.
    ChannelPost`. Created by :meth:`TelegramBotClient.fetch_channel_history`
    and consumed by :mod:`app.services.channel_history`. The dataclass
    is intentionally shallow — the dedup unique constraint is
    ``(channel_id, tg_message_id)`` and the rest of the fields are
    nullable on the model.
    """

    tg_message_id: int
    """TG ``message_id`` — UNIQUE per channel, paired with
    ``channel_id`` for the dedup index."""

    posted_at: datetime
    """``Message.date`` — UTC timestamp the post was published."""

    text: str | None = None
    """``Message.text`` / ``Message.caption`` — flattened body."""

    entities: list[dict[str, Any]] | None = None
    """TG ``MessageEntity[]`` list — preserves MarkdownV2 structure
    so the moderation pipeline (Sprint 3) can rebuild it."""

    has_media: bool = False
    """``True`` if the original post carried photo / video / document
    / audio / voice / animation media."""

    media_summary: dict[str, Any] | None = None
    """Compact descriptor of the media payload (kind + sizes) for
    the dashboard preview. Full media stays in TG; we don't copy it."""

    views_count: int | None = None
    """Channel post views — only populated for ``Message.views``;
    not exposed for forwarded messages, so the Bot API path leaves
    this ``None``. The user-bot path (PR #18) fills it in."""

    reactions_count: int | None = None
    """Sum of all reaction counts (``Message.reactions``)."""

    forwards_count: int | None = None
    """``Message.forward_count`` if available."""


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
    """Public Bot API surface used by the channel + history pipelines.

    PR #14 added ``get_chat`` / ``get_chat_member`` /
    ``get_chat_administrators``; PR #15 adds
    ``get_chat_member_count`` (refreshes ``subscribers_count`` on the
    global Channel row) and :meth:`fetch_channel_history` (paginated
    history backfill). PR #16 will extend this with ``set_webhook`` /
    dispatcher plumbing.
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

    async def get_chat_member_count(self, chat_id: int) -> int:
        """Return ``Chat.member_count`` (subscribers) for ``chat_id``.

        Used by the backfill task to refresh ``Channel.subscribers_count``
        before each ingest run. Same error contract as :meth:`get_chat`.
        """

    async def fetch_channel_history(
        self,
        chat_id: int,
        *,
        limit: int,
        from_message_id: int | None = None,
    ) -> list[ChannelPostSnapshot]:
        """Fetch up to ``limit`` posts from ``chat_id`` (PR #15).

        Returns posts ordered newest-first. ``from_message_id`` is the
        upper bound (exclusive) — pass ``None`` for the latest window.
        Implementations MAY return fewer than ``limit`` snapshots if
        the channel is shorter than the window, the bot is rate-limited,
        or some message_ids are unreachable via the Bot API. The service
        layer treats the returned set as the source-of-truth for one
        backfill run and dedups on ``(channel_id, tg_message_id)``.
        """

    async def get_me_id(self) -> int:
        """Return the bot's own ``user_id`` (used to look itself up)."""

    async def set_webhook(
        self,
        url: str,
        *,
        secret_token: str,
        allowed_updates: tuple[str, ...] = ("channel_post", "edited_channel_post"),
        drop_pending_updates: bool = False,
    ) -> bool:
        """Register ``url`` as the webhook target (PR #16).

        ``secret_token`` is forwarded as the ``X-Telegram-Bot-API-
        Secret-Token`` header on every webhook delivery — the value
        the API endpoint will check via :func:`hmac.compare_digest`.
        Returns ``True`` on success; transport failures map to
        :class:`TelegramTransportError`.
        """

    async def delete_webhook(self, *, drop_pending_updates: bool = False) -> bool:
        """Remove the registered webhook (PR #16).

        ``drop_pending_updates=True`` tells Telegram to drop the
        delivery backlog instead of replaying it once a new webhook
        is registered. Returns ``True`` on success.
        """

    async def get_webhook_info(self) -> WebhookInfo:
        """Read the bot's current webhook configuration (PR #16)."""

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

    async def get_chat_member_count(self, chat_id: int) -> int:
        from aiogram.exceptions import (
            TelegramBadRequest,
            TelegramNetworkError,
            TelegramRetryAfter,
        )

        try:
            return int(await self._bot.get_chat_member_count(chat_id))
        except TelegramBadRequest as exc:
            raise TelegramChannelNotFoundError(str(exc)) from exc
        except (TelegramNetworkError, TelegramRetryAfter) as exc:
            raise TelegramTransportError(str(exc)) from exc

    async def fetch_channel_history(
        self,
        chat_id: int,
        *,
        limit: int,
        from_message_id: int | None = None,
    ) -> list[ChannelPostSnapshot]:
        """Bot-API history fetch — currently a no-op (PR #15).

        Telegram's Bot API does not expose a ``getChatHistory`` method.
        Real history scraping happens through the MTProto user-bot in
        PR #18 (Pyrogram ``get_chat_history``) and the live ingest
        webhook in PR #16. PR #15 ships the infrastructure (Celery
        task, dedup, audit, API trigger, event publisher) and leaves
        this method as a typed stub that returns an empty list so the
        pipeline is fully exercised end-to-end without raising.
        """

        from app.core.logging import get_logger

        _logger = get_logger(__name__)
        _logger.info(
            "telegram_bot.fetch_channel_history.stub",
            chat_id=chat_id,
            limit=limit,
            from_message_id=from_message_id,
        )
        return []

    async def get_me_id(self) -> int:
        if self._me_id is not None:
            return self._me_id
        me = await self._bot.get_me()
        self._me_id = me.id
        return self._me_id

    async def set_webhook(
        self,
        url: str,
        *,
        secret_token: str,
        allowed_updates: tuple[str, ...] = ("channel_post", "edited_channel_post"),
        drop_pending_updates: bool = False,
    ) -> bool:
        from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter

        try:
            return bool(
                await self._bot.set_webhook(
                    url=url,
                    secret_token=secret_token,
                    allowed_updates=list(allowed_updates),
                    drop_pending_updates=drop_pending_updates,
                ),
            )
        except TelegramBadRequest as exc:
            # Mirror the get_chat 400 path — a bad URL is a config
            # issue, not a transient transport failure; we still
            # surface it as a transport error so the caller chooses
            # how to react (typed 502 vs. crash).
            raise TelegramTransportError(str(exc)) from exc
        except (TelegramNetworkError, TelegramRetryAfter) as exc:
            raise TelegramTransportError(str(exc)) from exc

    async def delete_webhook(self, *, drop_pending_updates: bool = False) -> bool:
        from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter

        try:
            return bool(
                await self._bot.delete_webhook(drop_pending_updates=drop_pending_updates),
            )
        except TelegramBadRequest as exc:
            raise TelegramTransportError(str(exc)) from exc
        except (TelegramNetworkError, TelegramRetryAfter) as exc:
            raise TelegramTransportError(str(exc)) from exc

    async def get_webhook_info(self) -> WebhookInfo:
        from datetime import UTC

        from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter

        try:
            info = await self._bot.get_webhook_info()
        except TelegramBadRequest as exc:
            raise TelegramTransportError(str(exc)) from exc
        except (TelegramNetworkError, TelegramRetryAfter) as exc:
            raise TelegramTransportError(str(exc)) from exc

        last_error_date = None
        raw_date = getattr(info, "last_error_date", None)
        if raw_date is not None:
            # aiogram returns datetime; the Bot API ships unix seconds.
            # Normalise to UTC-aware datetime regardless of the source.
            if isinstance(raw_date, datetime):
                last_error_date = (
                    raw_date if raw_date.tzinfo is not None else raw_date.replace(tzinfo=UTC)
                )
            else:
                last_error_date = datetime.fromtimestamp(int(raw_date), tz=UTC)

        return WebhookInfo(
            url=str(getattr(info, "url", "") or ""),
            has_custom_certificate=bool(getattr(info, "has_custom_certificate", False)),
            pending_update_count=int(getattr(info, "pending_update_count", 0) or 0),
            last_error_date=last_error_date,
            last_error_message=getattr(info, "last_error_message", None),
            allowed_updates=tuple(getattr(info, "allowed_updates", None) or ()),
        )

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
    member_count_by_chat: dict[int, int] = field(default_factory=dict)
    history_by_chat: dict[int, list[ChannelPostSnapshot]] = field(default_factory=dict)
    webhook_info: WebhookInfo = field(default_factory=lambda: WebhookInfo(url=""))
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

    async def get_chat_member_count(self, chat_id: int) -> int:
        self.call_log.append(("get_chat_member_count", (chat_id,)))
        if self.raise_transport_error:
            raise TelegramTransportError("mock transport error")
        if chat_id not in self.member_count_by_chat:
            raise TelegramChannelNotFoundError(
                f"chat {chat_id!r} not in mock member_count_by_chat",
            )
        return self.member_count_by_chat[chat_id]

    async def fetch_channel_history(
        self,
        chat_id: int,
        *,
        limit: int,
        from_message_id: int | None = None,
    ) -> list[ChannelPostSnapshot]:
        self.call_log.append(
            (
                "fetch_channel_history",
                (chat_id, limit, from_message_id),
            )
        )
        if self.raise_transport_error:
            raise TelegramTransportError("mock transport error")
        history = self.history_by_chat.get(chat_id, [])
        # Newest-first ordering matches the production contract
        # (TG returns ``message_id`` desc when paginating backwards).
        ordered = sorted(history, key=lambda p: p.tg_message_id, reverse=True)
        if from_message_id is not None:
            ordered = [p for p in ordered if p.tg_message_id < from_message_id]
        return ordered[:limit]

    async def get_me_id(self) -> int:
        return self.me_id

    async def set_webhook(
        self,
        url: str,
        *,
        secret_token: str,
        allowed_updates: tuple[str, ...] = ("channel_post", "edited_channel_post"),
        drop_pending_updates: bool = False,
    ) -> bool:
        self.call_log.append(
            (
                "set_webhook",
                (url, secret_token, tuple(allowed_updates), drop_pending_updates),
            ),
        )
        if self.raise_transport_error:
            raise TelegramTransportError("mock transport error")
        self.webhook_info = WebhookInfo(
            url=url,
            pending_update_count=0 if drop_pending_updates else self.webhook_info.pending_update_count,
            allowed_updates=tuple(allowed_updates),
        )
        return True

    async def delete_webhook(self, *, drop_pending_updates: bool = False) -> bool:
        self.call_log.append(("delete_webhook", (drop_pending_updates,)))
        if self.raise_transport_error:
            raise TelegramTransportError("mock transport error")
        self.webhook_info = WebhookInfo(url="")
        return True

    async def get_webhook_info(self) -> WebhookInfo:
        self.call_log.append(("get_webhook_info", ()))
        if self.raise_transport_error:
            raise TelegramTransportError("mock transport error")
        return self.webhook_info

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
    "ChannelPostSnapshot",
    "ChatMemberInfo",
    "MockTelegramBotClient",
    "TelegramBotClient",
    "TelegramChannelNotFoundError",
    "TelegramTransportError",
    "WebhookInfo",
    "get_telegram_bot_client",
    "set_telegram_bot_client_override",
]
