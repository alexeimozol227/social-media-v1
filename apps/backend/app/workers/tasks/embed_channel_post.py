"""Celery task: embed one channel post (PR #17).

docs/plans/phase1-sprint2-plan.md PR #17 — async embedding pipeline.
Triggering this task is the only sanctioned way to add a row to
``channel_post_embeddings``: the route handlers / live-ingest webhook
publish :class:`ChannelPostReceivedEvent` and a downstream consumer
(Sprint 3) enqueues :func:`embed_channel_post`. PR #17 only puts the
worker contract in place; the actual fan-out hook lands later.

Why a separate task instead of inlining into the webhook handler?

* Embedding is I/O-bound on the LLM provider and well below the Bot
  API webhook's 60s timeout budget; pushing it to Celery keeps the
  webhook latency stable + bounded.
* The task naturally retries on transient errors
  (:class:`LLMTimeoutError` / :class:`httpx.NetworkError`) without
  blocking the user-facing path.

The task wraps :func:`asyncio.run` around the async service because
the rest of the codebase (DB, provider) is async — mirroring the
PR #15 ``channel_backfill_history_task`` pattern.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import httpx
import structlog

from app.adapters.llm.base import LLMTimeoutError
from app.adapters.llm.polza import build_default_provider
from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.services.embeddings import EmbeddingPersistResult, EmbeddingsService
from app.workers.celery_app import celery_app

logger = structlog.get_logger(__name__)


# Maximum task body retries before giving up. Embeddings are
# best-effort signal — a permanently-failing post shouldn't pile up
# in the queue forever. The matching service-level audit row is
# written by the Sprint 3 fan-out hook.
MAX_RETRIES = 3

# Initial retry delay (seconds). Celery applies an exponential
# backoff multiplier internally when ``default_retry_delay`` is set
# on the task decorator.
RETRY_BACKOFF_SECONDS = 5


async def _run(channel_post_id: uuid.UUID) -> dict[str, Any]:
    """Async body of the Celery task.

    A fresh :class:`AsyncSession` is opened per invocation — Celery
    workers don't share the FastAPI dependency injection chain.
    The :class:`LLMProvider` is constructed via the env-aware
    factory so dev / CI run against the deterministic mock while
    production talks to Polza.
    """

    provider = build_default_provider()
    service = EmbeddingsService(
        provider=provider,
        model=settings.embedding_model,
        dim=settings.embedding_dim,
    )
    async with AsyncSessionLocal() as session:
        try:
            result: EmbeddingPersistResult = await service.embed_channel_post(
                session,
                channel_post_id,
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise

    return {
        "channel_post_id": str(channel_post_id),
        "model": result.model,
        "inserted": result.inserted,
        "updated": result.updated,
        "skipped": result.skipped,
    }


@celery_app.task(  # type: ignore[untyped-decorator]
    name="channel.embed_post",
    bind=True,
    acks_late=True,
    max_retries=MAX_RETRIES,
    default_retry_delay=RETRY_BACKOFF_SECONDS,
)
def embed_channel_post(
    self: Any,
    channel_post_id_str: str,
) -> dict[str, Any]:
    """Public Celery task entry point.

    ``channel_post_id_str`` is a JSON-serialisable string so the
    default JSON encoder doesn't need a UUID adapter (matches the
    pattern in :mod:`app.workers.tasks.channel_backfill`).

    Retry policy:

    * :class:`LLMTimeoutError` and :class:`httpx.NetworkError` are
      transient → ``self.retry`` with exponential backoff (Celery
      doubles ``default_retry_delay`` on each attempt). After
      :data:`MAX_RETRIES` attempts the original exception is
      re-raised and the task fails permanently.
    * Everything else propagates so the worker logs the stack and
      the operator's monitoring picks it up.
    """

    task_id = self.request.id or "unknown"
    logger.info(
        "embed_channel_post.task_started",
        task_id=task_id,
        channel_post_id=channel_post_id_str,
    )
    try:
        post_id = uuid.UUID(channel_post_id_str)
    except ValueError as exc:
        # The caller (Sprint 3 fan-out hook) should never enqueue a
        # malformed id, but a corrupt Celery message shouldn't crash
        # the worker — log + drop.
        logger.warning(
            "embed_channel_post.invalid_uuid",
            task_id=task_id,
            channel_post_id=channel_post_id_str,
            error=str(exc),
        )
        return {
            "channel_post_id": channel_post_id_str,
            "skipped": "invalid_uuid",
        }

    try:
        return asyncio.run(_run(post_id))
    except (LLMTimeoutError, httpx.NetworkError, httpx.TimeoutException) as exc:
        logger.info(
            "embed_channel_post.transient_failure_retry",
            task_id=task_id,
            channel_post_id=channel_post_id_str,
            error=exc.__class__.__name__,
        )
        # ``self.retry`` raises ``Retry`` which Celery interprets
        # as "re-queue with backoff". The exception we pass becomes
        # the recorded cause so the audit trail shows the actual
        # adapter error.
        raise self.retry(
            exc=exc,
            countdown=RETRY_BACKOFF_SECONDS * (2**self.request.retries),
        ) from exc


__all__ = [
    "embed_channel_post",
]
