"""Celery task: probe a single user-bot session (PR #18).

docs/05-tech-stack.md §5.2 + D40: a scheduled Celery beat job
(Sprint 8) fans this task out across every active user-bot session
so the pool rotates banned / dead sessions out before the
competitor-history workers stumble onto them.

PR #18 ships the task itself + retry policy. Wiring the beat
schedule is Sprint 8 — the pool admin endpoint enqueues
``userbot.healthcheck_session`` ad-hoc to verify a freshly-imported
session before marking it active.

Retry policy:

* :class:`UserBotTransportError` — transient network failure, retry
  once with a short backoff.
* :class:`UserBotAuthError` — session is dead, mark banned, no retry.
* Unknown session id — log + skip without retry (corrupt envelope).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import structlog
from sqlalchemy import select

from app.adapters.userbot import (
    UserBotAuthError,
    UserBotPool,
    UserBotTransportError,
    build_default_userbot_client,
)
from app.db.session import AsyncSessionLocal
from app.models.telegram_userbot_session import TelegramUserbotSession
from app.services.userbot_sessions import decrypt_session
from app.workers.celery_app import celery_app

logger = structlog.get_logger(__name__)


MAX_RETRIES = 1
RETRY_BACKOFF_SECONDS = 30


async def _run(session_id: uuid.UUID) -> dict[str, Any]:
    """Async body — load + decrypt + healthcheck + mark.

    Each call opens its own :class:`AsyncSession`; the pool
    lifecycle helpers (:meth:`UserBotPool.mark_healthcheck` /
    :meth:`mark_banned`) flush within that scope and the outer
    ``commit()`` persists.
    """

    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(TelegramUserbotSession).where(TelegramUserbotSession.id == session_id),
        )
        row = res.scalar_one_or_none()
        if row is None:
            logger.warning(
                "userbot_healthcheck.unknown_session",
                session_id=str(session_id),
            )
            return {"session_id": str(session_id), "skipped": "unknown_session"}

        decrypted = decrypt_session(row)
        client = build_default_userbot_client(
            api_id=decrypted.api_id,
            api_hash=decrypted.api_hash,
            session_string=decrypted.session_string,
            account_label=decrypted.account_label,
        )
        pool = UserBotPool(client_factory=lambda _row: client)

        try:
            ok = await client.healthcheck()
        except UserBotAuthError:
            await pool.mark_banned(row, session)
            await session.commit()
            return {
                "session_id": str(session_id),
                "result": "banned",
            }
        except UserBotTransportError:
            await session.rollback()
            raise
        finally:
            try:
                await client.close()
            except Exception:
                logger.warning(
                    "userbot_healthcheck.close_failed",
                    session_id=str(session_id),
                )

        await pool.mark_healthcheck(row, ok, session)
        await session.commit()
        return {
            "session_id": str(session_id),
            "result": "ok" if ok else "soft_fail",
        }


@celery_app.task(  # type: ignore[untyped-decorator]
    name="userbot.healthcheck_session",
    bind=True,
    acks_late=True,
    max_retries=MAX_RETRIES,
    default_retry_delay=RETRY_BACKOFF_SECONDS,
)
def healthcheck_session(
    self: Any,
    session_id_str: str,
) -> dict[str, Any]:
    """Public Celery entry-point.

    ``session_id_str`` is the user-bot row UUID. Invalid UUIDs /
    unknown rows return a skip envelope without retry — a corrupt
    Celery message shouldn't pin a worker.
    """

    task_id = self.request.id or "unknown"
    logger.info(
        "userbot_healthcheck.task_started",
        task_id=task_id,
        session_id=session_id_str,
    )
    try:
        sid = uuid.UUID(session_id_str)
    except ValueError as exc:
        logger.warning(
            "userbot_healthcheck.invalid_uuid",
            task_id=task_id,
            session_id=session_id_str,
            error=str(exc),
        )
        return {
            "session_id": session_id_str,
            "skipped": "invalid_uuid",
        }

    try:
        return asyncio.run(_run(sid))
    except UserBotTransportError as exc:
        logger.info(
            "userbot_healthcheck.transient_failure_retry",
            task_id=task_id,
            session_id=session_id_str,
            error=exc.__class__.__name__,
        )
        raise self.retry(
            exc=exc,
            countdown=RETRY_BACKOFF_SECONDS,
        ) from exc


__all__ = [
    "healthcheck_session",
]
