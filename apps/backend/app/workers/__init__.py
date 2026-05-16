"""Celery worker package (PR #15).

docs/05-tech-stack.md §2.4 + docs/plans/phase1-sprint2-plan.md PR #15:
async tasks run on a Celery worker fed by Redis. The first concrete
task is the channel-history backfill — subsequent PRs (#16 webhook
ingest, #17 embeddings, agent runners) will register here too.

The package is import-safe even when the ``celery`` wheel is not
installed (e.g. minimal CI envs) because the public surface is
behind :mod:`app.workers.celery_app`, which imports lazily.
"""

from __future__ import annotations
