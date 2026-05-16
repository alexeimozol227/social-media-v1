"""aiogram :class:`Dispatcher` factory for live channel ingest (PR #16).

docs/plans/phase1-sprint2-plan.md PR #16: the webhook endpoint
defers actual parsing to a Dispatcher / Router pair so the entry
point in :mod:`app.api.routes.integrations` stays thin and the
business logic lives in :mod:`app.services.channel_ingest`. The
factory pattern keeps a single Dispatcher instance per FastAPI app
\u2014 aiogram allocates its own middleware chain, so re-building it
per request would double per-update CPU.

Updates we accept (every other kind is dropped silently):

* ``channel_post`` \u2014 a new post landed in a channel where the bot
  is admin. Routed to :func:`ingest_live_post` with ``edited=False``.
* ``edited_channel_post`` \u2014 a previously-seen post was edited.
  Routed to :func:`ingest_live_post` with ``edited=True`` so the
  service layer takes the upsert / ``ChannelPostEditedEvent`` path.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from aiogram import Dispatcher
    from aiogram.types import Message

logger = get_logger(__name__)


# The service-layer callable we route to. Typed as a plain Callable
# so the Dispatcher doesn't import the service module at construction
# time (avoids circular imports between the integration layer and the
# service layer).
IngestCallable = Callable[..., Awaitable[Any]]


def build_dispatcher(ingest: IngestCallable) -> Dispatcher:
    """Return an aiogram :class:`Dispatcher` wired to ``ingest``.

    ``ingest`` receives ``(message, *, edited)`` and runs the
    deduplicated insert / upsert + event publish. Every other update
    type (private messages to the bot, callback queries, inline
    queries) is silently dropped \u2014 the webhook is registered with
    ``allowed_updates=("channel_post", "edited_channel_post")`` so
    Telegram should never deliver anything else, but we defend in
    depth.
    """

    from aiogram import Dispatcher, Router

    router = Router(name="channel_ingest")

    @router.channel_post()
    async def _on_channel_post(message: Message) -> None:
        logger.info(
            "telegram_webhook.channel_post",
            chat_id=message.chat.id,
            message_id=message.message_id,
        )
        await ingest(message, edited=False)

    @router.edited_channel_post()
    async def _on_edited_channel_post(message: Message) -> None:
        logger.info(
            "telegram_webhook.edited_channel_post",
            chat_id=message.chat.id,
            message_id=message.message_id,
        )
        await ingest(message, edited=True)

    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    return dispatcher


__all__ = ["build_dispatcher"]
