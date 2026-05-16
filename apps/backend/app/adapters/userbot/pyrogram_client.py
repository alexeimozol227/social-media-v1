"""Pyrogram-backed user-bot client (PR #18 — Sprint 3 skeleton).

docs/05-tech-stack.md §5.2 (D40): the production user-bot adapter
backs onto :mod:`pyrogram` (MTProto). PR #18 ships the contract +
skeleton; the actual ``Pyrogram.Client.get_chat_history()`` call
lands in Sprint 3 alongside the Brand Memory ingestor.

Why a skeleton + ``NotImplementedError("Sprint 3")`` and not the
real wiring?

* Pyrogram opens a long-lived MTProto socket per session; production
  deployment needs IP rotation (Bright Data) + cooldown rules that
  belong in Sprint 3.
* Unit tests run against :class:`MockUserBotClient` — wiring real
  Pyrogram from this PR would either pull the wheel into the test
  surface (we don't want that) or require gated integration tests
  (also out of scope for this sprint).

The mirror precedent is :class:`app.adapters.llm.polza.PolzaProvider`:
constructor accepts the decrypted creds, ``__repr__`` masks them,
and the data-fetching methods raise ``NotImplementedError("Sprint 3")``
until the production wiring lands.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.adapters.social.telegram_bot import ChannelPostSnapshot
from app.adapters.userbot.base import UserBotChannelInfo

if TYPE_CHECKING:  # pragma: no cover - typing only
    pass


_SPRINT_3_MESSAGE = (
    "Pyrogram wire-up — Sprint 3. Set USERBOT_CLIENT=mock for now or wait "
    "for the BrandMemory ingest PR."
)


class PyrogramUserBotClient:
    """Skeleton :class:`UserBotClient` implementation backed by Pyrogram.

    The constructor accepts the decrypted MTProto credentials
    (``api_id``, ``api_hash``, ``session_string``) and a logical
    ``account_label`` used in log lines / metrics. Pyrogram itself is
    imported lazily inside ``_get_pyrogram_client`` so the test suite
    can verify the skeleton's shape without depending on the wheel.

    The fetcher methods all raise :class:`NotImplementedError` until
    Sprint 3 — same pattern as :class:`PolzaProvider`.
    """

    def __init__(
        self,
        *,
        api_id: int,
        api_hash: str,
        session_string: str,
        account_label: str = "default",
    ) -> None:
        if not api_hash:
            raise ValueError("api_hash must be non-empty")
        if not session_string:
            raise ValueError("session_string must be non-empty")
        self._api_id = api_id
        self._api_hash = api_hash
        self._session_string = session_string
        self._account_label = account_label
        # Lazily-constructed Pyrogram client; ``Any`` because the
        # type only exists if the wheel is installed (Sprint 3).
        self._client: Any | None = None

    # ------------------------------------------------------------------
    # Skeleton implementations — raise until Sprint 3.
    # ------------------------------------------------------------------

    async def fetch_chat_info(self, identifier: str) -> UserBotChannelInfo:
        """Pyrogram ``client.get_chat(identifier)`` — Sprint 3."""

        _ = identifier
        raise NotImplementedError(_SPRINT_3_MESSAGE)

    async def fetch_channel_history(
        self,
        chat_id: int,
        *,
        limit: int,
        from_message_id: int | None = None,
    ) -> list[ChannelPostSnapshot]:
        """Pyrogram ``client.get_chat_history(...)`` — Sprint 3."""

        _ = chat_id, limit, from_message_id
        raise NotImplementedError(_SPRINT_3_MESSAGE)

    async def healthcheck(self) -> bool:
        """Pyrogram ``client.get_me()`` — Sprint 3."""

        raise NotImplementedError(_SPRINT_3_MESSAGE)

    async def close(self) -> None:
        """Shut down the Pyrogram client if it was created.

        Sprint 3 will call ``self._client.stop()`` once Pyrogram is
        wired up. PR #18 ships this as a no-op so the pool teardown
        path stays consistent regardless of which adapter is bound.
        """

        self._client = None

    # ------------------------------------------------------------------
    # Internal — masked repr so the session string doesn't leak in
    # log lines or pytest diffs.
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        masked = "***"
        return (
            f"PyrogramUserBotClient(api_id={self._api_id}, "
            f"api_hash={masked}, session={masked}, "
            f"account_label={self._account_label!r})"
        )


__all__ = [
    "PyrogramUserBotClient",
]
