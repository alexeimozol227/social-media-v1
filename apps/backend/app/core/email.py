"""Email transport (UniSender Go HTTP / SMTP / log).

Exposes a small :class:`EmailSender` Protocol so tests can substitute
a fake via ``app.dependency_overrides`` and the production / dev
choice is driven by config alone (resolution order in
:func:`get_email_sender`).

Implementations:

* :class:`LogEmailSender` — default in dev/CI/tests. Logs message
  *metadata* (not the body) at INFO level. The body carries the
  verification code / password-reset URL and must never hit the log
  stream in any environment that isn't strictly local. The audit
  guard in :class:`UniSenderGoEmailSender` (and a future production
  config gate) is the second line of defence.
* :class:`SmtpEmailSender` — aiosmtplib. Works with MailHog out of
  the box (``SMTP_HOST=localhost``, ``SMTP_PORT=1025``).
* :class:`UniSenderGoEmailSender` — HTTP POST to UniSender Go
  Transactional API. Used in production for RU/CIS-friendly
  deliverability (docs/05-tech-stack §3.6).
"""

from __future__ import annotations

from email.message import EmailMessage
from functools import lru_cache
from typing import Any, Protocol

import aiosmtplib
import httpx
import structlog

from app.core.config import settings

logger = structlog.get_logger(__name__)


class EmailSender(Protocol):
    async def send(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        purpose: str | None = None,
    ) -> None: ...


class LogEmailSender:
    """Default for dev / CI / tests.

    The message ``body`` is intentionally **not** logged — verification
    codes and password-reset URLs travel through the body. The dev
    transport used to log the body verbatim, which is fine on a
    developer's laptop but a PII / secret leak the moment that
    transport sees a non-dev environment.
    """

    async def send(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        purpose: str | None = None,
    ) -> None:
        del body  # body carries one-time secrets; never log.
        logger.info(
            "email.send",
            to=to,
            subject=subject,
            purpose=purpose,
            transport="log",
        )


class SmtpEmailSender:
    """Sends mail via aiosmtplib using the SMTP_* settings.

    Used for local development with MailHog (``SMTP_HOST=localhost``,
    ``SMTP_PORT=1025``) — the dashboard at http://localhost:8025 shows
    every message.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        sender: str,
        use_tls: bool,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username or None
        self._password = password or None
        self._sender = sender
        self._use_tls = use_tls

    async def send(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        purpose: str | None = None,
    ) -> None:
        message = EmailMessage()
        message["From"] = self._sender
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)

        await aiosmtplib.send(
            message,
            hostname=self._host,
            port=self._port,
            username=self._username,
            password=self._password,
            start_tls=self._use_tls,
        )
        logger.info(
            "email.send",
            to=to,
            subject=subject,
            purpose=purpose,
            transport="smtp",
        )


class UniSenderGoEmailSender:
    """Sends mail via UniSender Go Transactional API.

    https://godocs.unisender.ru/web-api-ref#email-send

    Errors are *not* re-raised: the calling service treats every send
    as best-effort because the row store has already been committed
    by then. A 5xx from UniSender does NOT block the user — they can
    request a new code / reset link, and the existing one still
    works until expiry.
    """

    _TIMEOUT_SECONDS = 5.0

    def __init__(
        self,
        *,
        api_key: str,
        api_url: str,
        from_email: str,
        from_name: str,
    ) -> None:
        self._api_key = api_key
        self._api_url = api_url.rstrip("/")
        self._from_email = from_email
        self._from_name = from_name

    async def send(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        purpose: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "message": {
                "subject": subject,
                "body": {"plaintext": body},
                "from_email": self._from_email,
                "from_name": self._from_name,
                "recipients": [{"email": to}],
            },
        }
        url = f"{self._api_url}/email/send.json"
        headers = {
            "X-API-KEY": self._api_key,
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=self._TIMEOUT_SECONDS) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            # Don't blow up the caller — see class docstring.
            logger.warning(
                "email.send_failed",
                to=to,
                subject=subject,
                purpose=purpose,
                transport="unisender_go",
                error=exc.__class__.__name__,
            )
            return
        logger.info(
            "email.send",
            to=to,
            subject=subject,
            purpose=purpose,
            transport="unisender_go",
        )


def _build_email_sender() -> EmailSender:
    """Resolve the configured transport.

    UniSender Go > SMTP > LogEmailSender. Order matches the comment
    block in :class:`app.core.config.Settings`.
    """

    if settings.unisender_api_key:
        return UniSenderGoEmailSender(
            api_key=settings.unisender_api_key,
            api_url=settings.unisender_api_url,
            from_email=settings.unisender_from_email,
            from_name=settings.unisender_from_name,
        )
    if settings.smtp_host:
        return SmtpEmailSender(
            host=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password,
            sender=settings.smtp_from,
            use_tls=settings.smtp_tls,
        )
    return LogEmailSender()


@lru_cache(maxsize=1)
def get_email_sender() -> EmailSender:
    """FastAPI dependency. Cached so each request reuses the same client."""

    return _build_email_sender()


__all__ = [
    "EmailSender",
    "LogEmailSender",
    "SmtpEmailSender",
    "UniSenderGoEmailSender",
    "get_email_sender",
]
