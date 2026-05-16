"""Integration metadata schemas.

Currently only the Telegram bot identity is exposed so the SPA's
"Connect channel" wizard can tell the user which bot to promote to
administrator.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class TelegramBotInfo(BaseModel):
    """Body of ``GET /v1/integrations/telegram/bot-info``.

    Returned regardless of whether the bot has been promoted to any
    specific channel — the username comes from the backend config so
    the frontend never has to hardcode ``@SocialMediaV1Bot``.
    """

    username: str = Field(
        description="Bot ``@username`` without the leading ``@``.",
    )
    deep_link: str = Field(
        description=(
            "Pre-built ``https://t.me/<username>?startchannel=true`` link the SPA can "
            "render as a CTA. Falls back to the plain ``t.me/<username>`` URL when the "
            "deep-link parameter is unsupported by the user's client."
        ),
    )


__all__ = ["TelegramBotInfo"]
