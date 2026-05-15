"""Feature-flag client wrapper (PR #8).

Implements D42 from ``docs/05-tech-stack.md`` ("Feature flags and
kill-switches — Unleash self-hosted, OSS") + П10 from
``docs/04-architecture.md §12.2`` (every auto-feature has a
``feature flag + kill-switch + rate-limit + safety-net``).

Why a wrapper
=============

The upstream ``unleash-client`` package has a chatty bootstrap step
(it polls the Unleash server, registers the app, and starts a metric
reporter thread on import). We want three things on top of that:

* **Safe fallback.** If ``UNLEASH_URL`` isn't configured (the
  dev / CI default) or the server is unreachable, ``is_enabled``
  returns the caller-supplied default rather than raising. The
  upstream client already supports this via the ``fallback_function``
  argument, but we wrap it so the call site stays a one-liner.
* **Lazy init.** We don't want every test (including ones that never
  touch flags) to pay the import-time cost of starting the Unleash
  poller thread.
* **Single source of truth for flag names.** The constants for flag
  names live here so a typo at the call site fails to import rather
  than silently returning the default.

The first flag (``auto_publish_enabled``) is a placeholder for the
real consumer (docs/07 §3 trial expiry + auto-publish guard in the
Publisher Agent). Its default is ``False``: if the Unleash control
plane is unavailable, we fail closed on potentially risky automation.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Protocol

from app.core.config import settings

logger = logging.getLogger(__name__)


# ---- Flag names ---------------------------------------------------

#: docs/07 §3 — trial expiry flips this off; docs/04 §12.2 names it
#: as the canonical Publisher Agent gating flag.
FLAG_AUTO_PUBLISH_ENABLED = "auto_publish_enabled"


# ---- Client wrapper ----------------------------------------------


class _UnleashLike(Protocol):
    """Minimal subset of the upstream client we actually call.

    Lets tests substitute a fake without depending on the upstream
    library being importable.
    """

    def is_enabled(
        self,
        feature_name: str,
        context: dict[str, Any] | None = ...,
        fallback_function: Any = ...,
    ) -> bool: ...

    def destroy(self) -> None: ...


class FeatureFlagClient:
    """Thread-safe lazy-init wrapper around ``unleash-client``.

    All public methods are safe to call before any Unleash server is
    reachable — they fall back to the supplied default in that case.
    The actual upstream client is only instantiated on first use and
    only if ``settings.unleash_url`` is configured.
    """

    def __init__(self) -> None:
        self._client: _UnleashLike | None = None
        self._init_attempted = False
        self._lock = threading.Lock()

    # -- internal helpers --

    def _initialise(self) -> None:
        """Create the underlying ``UnleashClient`` (idempotent)."""

        if self._init_attempted:
            return
        self._init_attempted = True

        if not settings.unleash_url:
            logger.info(
                "feature_flags.unleash_disabled",
                extra={"reason": "unleash_url_empty"},
            )
            return

        # Defer the import so tests / dev never pay for it.
        try:
            from UnleashClient import UnleashClient
        except ImportError:  # pragma: no cover — covered by the
            #                         dependency declaration in pyproject.
            logger.warning("feature_flags.unleash_client_unavailable")
            return

        custom_headers: dict[str, str] = {}
        if settings.unleash_api_token:
            custom_headers["Authorization"] = settings.unleash_api_token

        try:
            client = UnleashClient(
                url=settings.unleash_url,
                app_name=settings.unleash_app_name,
                environment=settings.unleash_environment,
                refresh_interval=settings.unleash_refresh_interval_seconds,
                custom_headers=custom_headers,
            )
            client.initialize_client()
        except Exception:
            #                         hot path because Unleash is down.
            logger.warning("feature_flags.unleash_init_failed", exc_info=True)
            return

        self._client = client

    # -- public API --

    def is_enabled(
        self,
        flag: str,
        *,
        default: bool,
        context: dict[str, Any] | None = None,
    ) -> bool:
        """Check whether ``flag`` is enabled for the given context.

        ``context`` follows the Unleash schema: ``userId`` selects the
        per-user rollout cohort; ``properties`` (a sub-dict) carries
        workspace / brand / environment metadata for custom strategies.

        ``default`` is the value to return when Unleash is unavailable,
        when ``flag`` is unknown, or when the upstream client raises.
        Failure modes never propagate — callers always get a ``bool``.
        """

        with self._lock:
            self._initialise()

        if self._client is None:
            return default

        try:
            return bool(
                self._client.is_enabled(
                    flag,
                    context or {},
                    fallback_function=lambda _name, _ctx: default,
                )
            )
        except Exception:
            #                         must not surface to the caller.
            logger.warning(
                "feature_flags.is_enabled_failed",
                extra={"flag": flag},
                exc_info=True,
            )
            return default

    def shutdown(self) -> None:
        """Stop the upstream polling thread (lifespan shutdown hook)."""

        with self._lock:
            if self._client is not None:
                try:
                    self._client.destroy()
                except Exception:
                    logger.warning("feature_flags.destroy_failed", exc_info=True)
                self._client = None


# Module-level singleton — the upstream client is process-wide by design.
_client = FeatureFlagClient()


def get_feature_flag_client() -> FeatureFlagClient:
    """FastAPI dependency / module entry point.

    Returning the singleton (rather than constructing per-request)
    keeps the upstream cache + polling thread shared across requests.
    """

    return _client


def is_enabled(
    flag: str,
    *,
    default: bool,
    user_id: str | None = None,
    workspace_id: str | None = None,
    brand_id: str | None = None,
) -> bool:
    """Convenience wrapper for the common call shape.

    Builds the standard Unleash context from our three primary
    scoping IDs (user / workspace / brand) so call sites stay terse.
    """

    properties: dict[str, str] = {}
    if workspace_id:
        properties["workspaceId"] = workspace_id
    if brand_id:
        properties["brandId"] = brand_id

    context: dict[str, Any] = {}
    if user_id:
        context["userId"] = user_id
    if properties:
        context["properties"] = properties

    return _client.is_enabled(flag, default=default, context=context)


__all__ = [
    "FLAG_AUTO_PUBLISH_ENABLED",
    "FeatureFlagClient",
    "get_feature_flag_client",
    "is_enabled",
]
