"""Celery task: backfill a channel's post history (PR #15).

docs/plans/phase1-sprint2-plan.md PR #15 — runs ``service.run_backfill``
in a separate worker process so the connect-channel HTTP handler can
return 202 instantly without waiting on Bot API I/O.

The task wraps an ``asyncio.run`` over the async service layer because
the rest of the codebase (DB, Redis, adapter) is already async; we
don't want to maintain two parallel sync implementations. ``acks_late
+ task_acks_late = True`` (set in :mod:`app.workers.celery_app`) plus
the idempotent dedup on ``(channel_id, tg_message_id)`` means a
worker crash mid-task safely retries on next pickup without creating
duplicate posts.

Audit trail: the task records two ``audit_events`` rows —
``channel.backfill_started`` (right after we resolve the binding) and
``channel.backfill_completed`` (after the run returns or fails). Both
are owned by the user who triggered the backfill so the workspace
admin can correlate the run.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import structlog

from app.adapters.social import (
    TelegramChannelNotFoundError,
    TelegramTransportError,
    get_telegram_bot_client,
)
from app.core.redis import get_redis
from app.db.session import AsyncSessionLocal
from app.errors import (
    AppError,
    ChannelBackfillNotConfiguredError,
    ChannelNotConnectedError,
    ChannelNotFoundError,
    TelegramAPIError,
)
from app.models.audit_event import AuditSeverity
from app.services import audit as audit_service
from app.services.channel_history import BackfillSummary, run_backfill
from app.workers.celery_app import celery_app

logger = structlog.get_logger(__name__)


# Maximum task body retries; defensive so a permanent adapter
# misconfiguration doesn't spin forever.
MAX_RETRIES = 3


async def _run(
    *,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID,
    brand_id: uuid.UUID,
    workspace_channel_id: uuid.UUID,
    limit: int,
    task_id: str,
    from_message_id: int | None,
) -> dict[str, Any]:
    """Async body of the Celery task.

    A fresh ``AsyncSession`` is opened per invocation — Celery
    workers don't share the FastAPI dependency injection chain, so
    we can't reuse :func:`app.api.deps.get_db` here. Redis + Bot
    adapter come from the same module-level providers the route
    handlers use, so test overrides apply uniformly.
    """

    redis = get_redis()
    client = get_telegram_bot_client()

    async with AsyncSessionLocal() as session:
        try:
            # Audit row for the kick-off — useful when the task takes
            # multiple minutes and the user wonders what's happening.
            await audit_service.record(
                session,
                event_type="channel.backfill_started",
                severity=AuditSeverity.INFO,
                user_id=user_id,
                workspace_id=workspace_id,
                metadata={
                    "workspace_channel_id": str(workspace_channel_id),
                    "brand_id": str(brand_id),
                    "limit": limit,
                    "from_message_id": from_message_id,
                    "task_id": task_id,
                },
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise

        summary: BackfillSummary
        try:
            summary = await run_backfill(
                session,
                redis,
                client,
                user_id=user_id,
                workspace_id=workspace_id,
                brand_id=brand_id,
                workspace_channel_id=workspace_channel_id,
                limit=limit,
                task_id=task_id,
                from_message_id=from_message_id,
            )
            await session.commit()
        except (
            ChannelNotFoundError,
            ChannelNotConnectedError,
            TelegramAPIError,
            ChannelBackfillNotConfiguredError,
        ) as exc:
            await session.rollback()
            await _record_failure(
                user_id=user_id,
                workspace_id=workspace_id,
                workspace_channel_id=workspace_channel_id,
                brand_id=brand_id,
                limit=limit,
                task_id=task_id,
                status=_failure_status_for(exc),
            )
            raise
        except (TelegramChannelNotFoundError, TelegramTransportError) as exc:
            # The service layer normalises these into ``AppError``
            # subclasses, but catch the raw adapter exceptions too in
            # case a future caller skips the helper.
            await session.rollback()
            await _record_failure(
                user_id=user_id,
                workspace_id=workspace_id,
                workspace_channel_id=workspace_channel_id,
                brand_id=brand_id,
                limit=limit,
                task_id=task_id,
                status="transport_error",
            )
            raise TelegramAPIError() from exc

        # Happy path — write the success audit row in a separate
        # transaction so its visibility doesn't depend on whether the
        # caller already committed the ingest.
        async with AsyncSessionLocal() as audit_session:
            try:
                await audit_service.record(
                    audit_session,
                    event_type="channel.backfill_completed",
                    severity=AuditSeverity.INFO,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    metadata={
                        "workspace_channel_id": str(summary.workspace_channel_id),
                        "channel_id": str(summary.channel_id),
                        "brand_id": str(brand_id),
                        "task_id": task_id,
                        "status": summary.status,
                        "fetched_count": summary.fetched_count,
                        "inserted_count": summary.inserted_count,
                        "duplicate_count": summary.duplicate_count,
                    },
                )
                await audit_session.commit()
            except Exception:
                await audit_session.rollback()
                raise

        return {
            "task_id": task_id,
            "workspace_channel_id": str(summary.workspace_channel_id),
            "channel_id": str(summary.channel_id),
            "status": summary.status,
            "fetched_count": summary.fetched_count,
            "inserted_count": summary.inserted_count,
            "duplicate_count": summary.duplicate_count,
        }


def _failure_status_for(exc: AppError) -> str:
    """Map a typed error to the ``status`` slug used in audit rows."""

    if isinstance(exc, ChannelNotFoundError):
        return "not_found"
    if isinstance(exc, ChannelNotConnectedError):
        return "not_connected"
    if isinstance(exc, TelegramAPIError):
        return "transport_error"
    if isinstance(exc, ChannelBackfillNotConfiguredError):
        return "adapter_unsupported"
    return "error"


async def _record_failure(
    *,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID,
    workspace_channel_id: uuid.UUID,
    brand_id: uuid.UUID,
    limit: int,
    task_id: str,
    status: str,
) -> None:
    """Best-effort failure audit — never raises out of the task."""

    async with AsyncSessionLocal() as session:
        try:
            await audit_service.record(
                session,
                event_type="channel.backfill_completed",
                severity=AuditSeverity.WARNING,
                user_id=user_id,
                workspace_id=workspace_id,
                metadata={
                    "workspace_channel_id": str(workspace_channel_id),
                    "brand_id": str(brand_id),
                    "task_id": task_id,
                    "status": status,
                    "limit": limit,
                },
            )
            await session.commit()
        except Exception as inner:  # pragma: no cover - defensive
            await session.rollback()
            logger.warning(
                "channel_backfill.audit_failed",
                error=inner.__class__.__name__,
                task_id=task_id,
            )


@celery_app.task(  # type: ignore[untyped-decorator]
    name="channel.backfill_history",
    bind=True,
    max_retries=MAX_RETRIES,
    default_retry_delay=30,
)
def backfill_channel_history_task(
    self: Any,
    *,
    user_id: str,
    workspace_id: str,
    brand_id: str,
    workspace_channel_id: str,
    limit: int,
    from_message_id: int | None = None,
) -> dict[str, Any]:
    """Public Celery task entry point.

    Arguments are passed as JSON-serialisable strings so Celery's
    default JSON encoder doesn't need a UUID adapter.

    The task body lives in the async :func:`_run` helper to keep
    the sync/async boundary explicit. We rely on ``asyncio.run`` so
    every retry gets its own event loop — running tasks share
    nothing across attempts, which is important because the
    ``aiogram`` ``Bot`` object holds a stateful aiohttp session.
    """

    task_id = self.request.id or "unknown"
    logger.info(
        "channel_backfill.task_started",
        task_id=task_id,
        workspace_channel_id=workspace_channel_id,
        limit=limit,
    )
    return asyncio.run(
        _run(
            user_id=uuid.UUID(user_id),
            workspace_id=uuid.UUID(workspace_id),
            brand_id=uuid.UUID(brand_id),
            workspace_channel_id=uuid.UUID(workspace_channel_id),
            limit=limit,
            task_id=task_id,
            from_message_id=from_message_id,
        )
    )


__all__ = [
    "backfill_channel_history_task",
]
