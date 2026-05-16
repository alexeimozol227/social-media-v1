"""User-bot session management service (PR #18).

Sits between the Fernet crypto helper and the
:class:`TelegramUserbotSession` ORM model so the admin API (Sprint 8)
and the Celery healthcheck task share one canonical path for
encrypting / decrypting credentials.

The decrypted dataclass exists so the rest of the codebase can
type-check against an unambiguous "plaintext creds" object instead
of passing three loose strings around.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_text, encrypt_text
from app.models.telegram_userbot_session import TelegramUserbotSession

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class DecryptedSession:
    """Plaintext MTProto credentials decoded from a session row.

    Kept frozen so callers can't mutate it in place and leak the
    plaintext into another scope by accident. The fields mirror what
    Pyrogram's ``Client(...)`` constructor needs.
    """

    session_id: uuid.UUID
    phone_number: str
    account_label: str
    api_id: int
    api_hash: str
    session_string: str


async def register_session(
    session: AsyncSession,
    *,
    phone_number: str,
    account_label: str,
    api_id: int,
    api_hash: str,
    session_string: str,
    notes: str | None = None,
) -> TelegramUserbotSession:
    """Encrypt plaintext credentials and insert a new session row.

    Called by the admin endpoint (Sprint 8). PR #18 ships the helper
    so unit tests + Celery healthcheck tests can populate the table
    without poking at the ORM directly.
    """

    if not phone_number:
        raise ValueError("phone_number must be non-empty")
    if not account_label:
        raise ValueError("account_label must be non-empty")
    if not session_string:
        raise ValueError("session_string must be non-empty")

    row = TelegramUserbotSession(
        phone_number=phone_number,
        account_label=account_label,
        api_id_encrypted=encrypt_text(str(api_id)),
        api_hash_encrypted=encrypt_text(api_hash),
        session_encrypted=encrypt_text(session_string),
        status="active",
        usage_count_24h=0,
        notes=notes,
    )
    session.add(row)
    await session.flush()
    logger.info(
        "userbot_sessions.registered",
        session_id=str(row.id),
        account_label=account_label,
        phone_suffix=phone_number[-4:] if len(phone_number) >= 4 else phone_number,
    )
    return row


def decrypt_session(row: TelegramUserbotSession) -> DecryptedSession:
    """Return a :class:`DecryptedSession` with the credentials in plaintext.

    Caller-side typed wrapper — the result is meant to be consumed by
    a factory call (:func:`build_default_userbot_client`) and then
    discarded. Don't persist :class:`DecryptedSession` instances or
    log them.
    """

    api_id_str = decrypt_text(row.api_id_encrypted)
    try:
        api_id = int(api_id_str)
    except ValueError as exc:
        raise ValueError(
            f"api_id decrypted to a non-integer value for session {row.id}",
        ) from exc
    return DecryptedSession(
        session_id=row.id,
        phone_number=row.phone_number,
        account_label=row.account_label,
        api_id=api_id,
        api_hash=decrypt_text(row.api_hash_encrypted),
        session_string=decrypt_text(row.session_encrypted),
    )


async def list_sessions(
    session: AsyncSession,
    *,
    include_disabled: bool = False,
) -> list[TelegramUserbotSession]:
    """Admin lens — return every userbot session row WITHOUT decryption.

    Used by the Sprint 8 admin endpoint; PR #18 exposes it for the
    unit-test surface only. The query never touches the encrypted
    bytes — the response shape (built by the admin schema) masks the
    sensitive columns.
    """

    stmt = select(TelegramUserbotSession).order_by(
        TelegramUserbotSession.account_label.asc(),
    )
    if not include_disabled:
        stmt = stmt.where(TelegramUserbotSession.status != "disabled")
    res = await session.execute(stmt)
    return list(res.scalars().all())


__all__ = [
    "DecryptedSession",
    "decrypt_session",
    "list_sessions",
    "register_session",
]
