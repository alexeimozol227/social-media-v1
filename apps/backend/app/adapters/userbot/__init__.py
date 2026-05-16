"""User-bot adapter package (PR #18).

Public surface: the Protocol + the typed errors + the two concrete
clients + the rotation pool. The factory :func:`build_default_userbot_client`
mirrors :func:`app.adapters.llm.polza.build_default_provider`: in
``mock`` mode it returns a deterministic :class:`MockUserBotClient`;
in ``pyrogram`` mode it constructs a :class:`PyrogramUserBotClient`
from the decrypted credentials. The pool uses this factory via a
closure so the row → client step stays decoupled from row selection.
"""

from __future__ import annotations

from app.adapters.userbot.base import (
    UserBotAuthError,
    UserBotChannelInfo,
    UserBotChannelNotFoundError,
    UserBotChannelPrivateError,
    UserBotClient,
    UserBotError,
    UserBotFloodWaitError,
    UserBotTransportError,
)
from app.adapters.userbot.mock import MockUserBotClient
from app.adapters.userbot.pool import (
    ClientFactory,
    UserBotPool,
    UserBotPoolEmptyError,
)
from app.adapters.userbot.pyrogram_client import PyrogramUserBotClient
from app.core.config import settings


def build_default_userbot_client(
    *,
    api_id: int | None = None,
    api_hash: str | None = None,
    session_string: str | None = None,
    account_label: str = "default",
) -> UserBotClient:
    """Return the user-bot client configured for the current environment.

    ``USERBOT_CLIENT=mock`` (the default) yields :class:`MockUserBotClient`
    with the canned fixtures; ``USERBOT_CLIENT=pyrogram`` yields a
    :class:`PyrogramUserBotClient` constructed from the supplied
    decrypted credentials. Passing ``mock`` mode credentials is a
    no-op — the mock doesn't consult them.
    """

    if settings.userbot_client == "pyrogram":
        if api_id is None or api_hash is None or session_string is None:
            msg = (
                "Pyrogram user-bot client requires api_id / api_hash / session_string. "
                "Pass them after decrypting the row via decrypt_session()."
            )
            raise ValueError(msg)
        return PyrogramUserBotClient(
            api_id=api_id,
            api_hash=api_hash,
            session_string=session_string,
            account_label=account_label,
        )
    return MockUserBotClient()


__all__ = [
    "ClientFactory",
    "MockUserBotClient",
    "PyrogramUserBotClient",
    "UserBotAuthError",
    "UserBotChannelInfo",
    "UserBotChannelNotFoundError",
    "UserBotChannelPrivateError",
    "UserBotClient",
    "UserBotError",
    "UserBotFloodWaitError",
    "UserBotPool",
    "UserBotPoolEmptyError",
    "UserBotTransportError",
    "build_default_userbot_client",
]
