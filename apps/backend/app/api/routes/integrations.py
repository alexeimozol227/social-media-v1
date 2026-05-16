"""Integration metadata routes.

Sit under ``/v1/integrations/*`` and expose just enough static config
to let the SPA render integration-specific UI without baking server
secrets / usernames into the frontend bundle.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentUser
from app.core.config import settings
from app.errors import TelegramBotNotConfiguredError
from app.schemas.integrations import TelegramBotInfo

router = APIRouter()


@router.get(
    "/v1/integrations/telegram/bot-info",
    response_model=TelegramBotInfo,
    summary="Telegram bot identity used by the channel-connect wizard",
)
async def telegram_bot_info(_user: CurrentUser) -> TelegramBotInfo:
    """Return the configured Telegram bot ``@username`` and a deep link.

    Authenticated to keep the bot username out of unauthenticated
    enumeration — it's not secret, but there's no business reason to
    surface it before sign-in either. The SPA hits this once on the
    "Connect channel" wizard mount and caches the result for the
    session.
    """

    raw = settings.telegram_bot_username.strip()
    if not raw:
        raise TelegramBotNotConfiguredError()
    # Tolerate both ``@SocialMediaV1Bot`` and ``SocialMediaV1Bot`` in env.
    username = raw.lstrip("@")
    deep_link = f"https://t.me/{username}?startchannel=true"
    return TelegramBotInfo(username=username, deep_link=deep_link)


__all__ = ["router"]
