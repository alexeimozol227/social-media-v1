"""External-platform integration routes (PR #16).

docs/plans/phase1-sprint2-plan.md PR #16: Telegram delivers
``channel_post`` / ``edited_channel_post`` updates to
``POST /v1/integrations/telegram/webhook``. The handler:

1. Validates the ``X-Telegram-Bot-API-Secret-Token`` header against
   :attr:`Settings.telegram_webhook_secret` via
   :func:`hmac.compare_digest`. Missing header / mismatch / empty
   server-side secret all map to 401
   :class:`TelegramWebhookUnauthorizedError` \u2014 a forgotten env var
   must NOT accidentally expose the endpoint to the open internet.
2. Parses the JSON body into an aiogram :class:`Update` and routes
   it through a process-wide :class:`Dispatcher` (lazily built on
   first use). The dispatcher delegates to
   :func:`ingest_live_post` for the actual DB + event-bus side
   effects.
3. Returns ``204 No Content`` on every successful delivery,
   including malformed / unknown update types. Telegram retries
   webhook deliveries on any non-2xx response \u2014 we must surface
   the failure only when we genuinely can't read the secret.
"""

from __future__ import annotations

import hmac
import json
from typing import Any

import structlog
from fastapi import APIRouter, Header, Request, Response, status

from app.api.deps import DbSession
from app.core.config import settings
from app.core.redis import get_redis
from app.errors import TelegramWebhookUnauthorizedError
from app.integrations.telegram import build_dispatcher
from app.services.channel_ingest import ingest_live_post

logger = structlog.get_logger(__name__)


router = APIRouter()


# Header name documented in
# https://core.telegram.org/bots/api#setwebhook \u2014 hyphen-cased per
# RFC 7230; FastAPI normalises header lookups to lower-case so the
# constant is purely for readability + tests.
TELEGRAM_SECRET_HEADER = "X-Telegram-Bot-API-Secret-Token"


def _verify_secret(received: str | None) -> None:
    """Compare ``received`` against ``settings.telegram_webhook_secret``.

    Raises :class:`TelegramWebhookUnauthorizedError` (401) when the
    secret is missing on the server (deployment hasn't configured
    it yet) or the header / body don't match. The comparison is
    :func:`hmac.compare_digest` to defeat the timing-channel attack
    \u2014 a naive ``==`` would leak a few microseconds per matching
    byte and let a remote attacker brute-force the secret one
    character at a time.
    """

    expected = settings.telegram_webhook_secret
    if not expected:
        # Empty server-side secret \u2014 lock the endpoint down.
        raise TelegramWebhookUnauthorizedError()
    if not received:
        raise TelegramWebhookUnauthorizedError()
    if not hmac.compare_digest(expected, received):
        raise TelegramWebhookUnauthorizedError()


@router.post(
    "/v1/integrations/telegram/webhook",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Telegram Bot API webhook entrypoint (PR #16)",
    include_in_schema=False,
)
async def telegram_webhook(
    request: Request,
    db: DbSession,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> Response:
    """Handle one Telegram webhook delivery.

    The endpoint is unauthenticated for *users* \u2014 the only auth
    check is the shared secret in :attr:`TELEGRAM_SECRET_HEADER`.
    We deliberately don't go through :func:`get_current_user` so a
    delivery from Telegram doesn't need a JWT.
    """

    _verify_secret(x_telegram_bot_api_secret_token)

    raw_body = await request.body()
    try:
        payload: dict[str, Any] = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError:
        # Malformed JSON \u2014 swallow + 204 so Telegram doesn't retry
        # forever on a poisoned delivery.
        logger.warning("telegram_webhook.invalid_json")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    if not isinstance(payload, dict):
        logger.warning("telegram_webhook.invalid_payload_shape")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # Lazy aiogram import keeps the FastAPI startup graph independent
    # of the aiogram wheel \u2014 unit tests that mock the dispatcher
    # don't need aiogram in their import chain.
    from aiogram.types import Update

    try:
        update = Update.model_validate(payload)
    except Exception as exc:  # pragma: no cover - aiogram raises ValidationError
        logger.warning(
            "telegram_webhook.invalid_update",
            error=exc.__class__.__name__,
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    redis = get_redis()

    async def _ingest(message: Any, *, edited: bool) -> None:
        # ``message`` is bound at dispatch time to either ``update.
        # channel_post`` or ``update.edited_channel_post`` \u2014 both
        # are :class:`aiogram.types.Message`.
        await ingest_live_post(db, redis, message, edited=edited)

    dispatcher = _get_dispatcher(request.app, ingest=_ingest)

    # Aiogram's ``feed_webhook_update`` returns the dispatcher's
    # response \u2014 we don't echo it back to Telegram (we already
    # said 204) but we await it so any per-update DB writes commit
    # within this request's transaction.
    try:
        await dispatcher.feed_webhook_update(_DummyBot(), update)
    except Exception:
        # Re-raise so the route's transaction rolls back \u2014 the
        # error middleware logs + Sentry-tags it. Telegram will
        # retry, which is the expected outcome.
        logger.exception(
            "telegram_webhook.dispatch_failed",
            update_id=update.update_id,
        )
        raise

    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Dispatcher singleton + lightweight Bot shim
# ---------------------------------------------------------------------------


def _get_dispatcher(app: Any, *, ingest: Any) -> Any:
    """Build (or fetch) the per-FastAPI-app Dispatcher singleton.

    aiogram's :class:`Dispatcher` is stateful (middleware chain +
    handler indices) so building it once per process avoids the
    per-request CPU cost. We store it on ``app.state`` so tests that
    swap out :func:`ingest_live_post` (via dependency overrides /
    monkeypatch) get a fresh dispatcher per app instance.
    """

    # ``ingest`` is a closure capturing the request-scoped DB session,
    # so a stale dispatcher would route to a stale session. Re-build
    # whenever the closure changes \u2014 the build is cheap (Router +
    # two handler registrations).
    dispatcher = build_dispatcher(ingest)
    app.state.telegram_dispatcher = dispatcher
    return dispatcher


class _DummyBot:
    """Stand-in for :class:`aiogram.Bot` when only routing is needed.

    :meth:`Dispatcher.feed_webhook_update` requires a ``Bot`` arg
    because it threads through the middleware chain. We don't make
    any Bot API calls inside the channel ingest handlers \u2014 the
    service layer only writes to the DB and the event bus \u2014 so a
    minimal stand-in is sufficient. Production deployments still
    have a real ``Bot`` for the :func:`set_webhook` lifecycle call,
    but that lives in the adapter, not on the request path.
    """

    @property
    def id(self) -> int:
        return 0


__all__ = ["router"]
