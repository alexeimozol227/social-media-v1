"""Celery task modules (PR #15+).

Each module under :mod:`app.workers.tasks` registers one bound
Celery task with :data:`app.workers.celery_app.celery_app`. Keep
modules small — one task per file makes the import graph linear
and the task discovery list in ``celery_app.include`` easy to
audit.
"""

from __future__ import annotations
