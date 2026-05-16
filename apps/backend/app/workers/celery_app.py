"""Celery application factory (PR #15).

docs/05-tech-stack.md §2.4 + docs/plans/phase1-sprint2-plan.md PR #15:

* Broker + result backend = Redis (different logical DBs from the
  pubsub event bus so queue traffic and event-bus traffic don't
  collide on the same channel namespace).
* Task discovery: explicit ``include`` list — we don't autodiscover
  to keep the import surface predictable in tests + linting.
* ``always_eager`` flips on for tests via ``conftest.py`` so unit
  tests run the task body synchronously without spinning up a
  worker process.

Production runs ``celery -A app.workers.celery_app worker -l info``
in a sibling container; ``make dev-worker`` does the same locally.
"""

from __future__ import annotations

from functools import lru_cache

from celery import Celery  # type: ignore[import-untyped]

from app.core.config import settings


@lru_cache(maxsize=1)
def _build() -> Celery:
    """Construct the process-wide Celery app exactly once.

    Memoized so importing this module from a route handler (to call
    ``.delay()``) doesn't repeatedly re-instantiate the broker
    connection pool. The lru_cache wrapper also makes the function
    cheap to mock — tests reach in via ``celery_app.conf.update``.
    """

    app = Celery(
        "social_media_v1",
        broker=settings.celery_broker_url,
        backend=settings.celery_result_backend,
        # Explicit include over autodiscover — keeps the import graph
        # static so static analysis can follow it.
        include=[
            "app.workers.tasks.channel_backfill",
            "app.workers.tasks.embed_channel_post",
        ],
    )

    app.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        task_soft_time_limit=settings.celery_task_soft_time_limit,
        task_time_limit=settings.celery_task_time_limit,
        task_always_eager=settings.celery_task_always_eager,
        task_eager_propagates=settings.celery_task_always_eager,
        # Late ack so a worker crash mid-task re-queues the message
        # rather than dropping it. Backfill is idempotent on
        # (channel_id, tg_message_id) so retries are safe.
        task_acks_late=True,
        worker_prefetch_multiplier=1,
        # Result TTL: 1h is enough for the SPA to poll completion;
        # longer than the task hard limit so a slow run doesn't lose
        # its result row.
        result_expires=3600,
    )

    return app


celery_app: Celery = _build()
"""Module-level singleton. Import this from route handlers + tests."""


__all__ = ["celery_app"]
