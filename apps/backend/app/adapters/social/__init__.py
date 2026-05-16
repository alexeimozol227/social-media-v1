"""Social-platform adapters (Telegram on MVP)."""

from app.adapters.social.telegram_bot import (
    AiogramTelegramBotClient,
    ChannelInfo,
    ChannelPostSnapshot,
    ChatMemberInfo,
    MockTelegramBotClient,
    TelegramBotClient,
    TelegramChannelNotFoundError,
    TelegramTransportError,
    get_telegram_bot_client,
)

__all__ = [
    "AiogramTelegramBotClient",
    "ChannelInfo",
    "ChannelPostSnapshot",
    "ChatMemberInfo",
    "MockTelegramBotClient",
    "TelegramBotClient",
    "TelegramChannelNotFoundError",
    "TelegramTransportError",
    "get_telegram_bot_client",
]
