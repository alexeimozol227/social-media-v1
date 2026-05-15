"""Sentry initialization (no-op when DSN is empty)."""

from __future__ import annotations

import sentry_sdk

from app.core.config import settings


def init_sentry(component: str = "api") -> None:
    """Initialize Sentry only when ``SENTRY_DSN`` is provided.

    Local dev / CI / tests run without a DSN; we want this to be a
    no-op there, not a startup failure.
    """

    if not settings.sentry_dsn:
        return
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        traces_sample_rate=0.1,
        profiles_sample_rate=0.0,
    )
    sentry_sdk.set_tag("component", component)
