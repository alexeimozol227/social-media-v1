"""Feature-flag client (D42 in docs/04, П10 in docs/04 §2).

Wraps the ``UnleashClient`` SDK. When ``UNLEASH_URL`` is unset
(local dev, CI) a simple in-memory fallback returns static defaults
so the rest of the codebase can always call ``is_enabled(...)``
without branching on "is Unleash configured?".

The first flag shipped with this module: ``enable_auto_publish``
(docs/05 §1 D42 + docs/04 §12.2 П10 — every auto-fiche requires
a feature flag + kill-switch + rate-limit + safety-net).
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Well-known flag names (D42, П10)
# ---------------------------------------------------------------------------
ENABLE_AUTO_PUBLISH = "enable_auto_publish"

# Default values for flags when Unleash is not available.
_DEFAULTS: dict[str, bool] = {
    ENABLE_AUTO_PUBLISH: False,
}

# ---------------------------------------------------------------------------
# In-memory fallback (dev / CI / when Unleash is down)
# ---------------------------------------------------------------------------

_overrides: dict[str, bool] = {}


class _InMemoryFlagClient:
    """Trivial feature-flag store used when Unleash is not configured."""

    def is_enabled(self, flag_name: str, *, default: bool | None = None) -> bool:
        if flag_name in _overrides:
            return _overrides[flag_name]
        if default is not None:
            return default
        return _DEFAULTS.get(flag_name, False)

    async def close(self) -> None:
        """No-op for in-memory client."""
        return


# ---------------------------------------------------------------------------
# Unleash-backed client (production)
# ---------------------------------------------------------------------------

_client: _InMemoryFlagClient | Any = None
_unleash_started: bool = False


async def _init_unleash() -> Any:
    """Lazily initialise the real Unleash SDK client.

    Importing ``UnleashClient`` is guarded so the ``unleash-client``
    package is only a runtime dependency when ``UNLEASH_URL`` is set.
    """

    try:
        from UnleashClient import UnleashClient  # type: ignore[import-not-found]
    except ImportError:
        logger.warning(
            "feature_flags.unleash_import_failed",
            hint="pip install unleash-client; falling back to in-memory defaults",
        )
        return _InMemoryFlagClient()

    uc = UnleashClient(
        url=settings.unleash_url,
        app_name=settings.unleash_app_name,
        instance_id=settings.unleash_instance_id,
        custom_headers={"Authorization": settings.unleash_api_key}
        if settings.unleash_api_key
        else {},
        refresh_interval=settings.unleash_refresh_interval,
    )

    # UnleashClient.initialize() is blocking; run in a thread so
    # we don't stall the async event loop.
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, uc.initialize_client)
    logger.info("feature_flags.unleash_connected", url=settings.unleash_url)
    return uc


async def get_flag_client() -> _InMemoryFlagClient | Any:
    """Return the process-wide feature-flag client (lazy init)."""

    global _client, _unleash_started

    if _client is not None:
        return _client

    if settings.unleash_url:
        _client = await _init_unleash()
        _unleash_started = True
    else:
        _client = _InMemoryFlagClient()
        logger.info(
            "feature_flags.using_in_memory",
            hint="Set UNLEASH_URL to connect to a real Unleash server",
        )

    return _client


def is_enabled(flag_name: str, *, default: bool | None = None) -> bool:
    """Check whether *flag_name* is enabled.

    Synchronous convenience wrapper so callers don't need to ``await``
    on every hot-path check. If the client hasn't been initialised yet
    (e.g. during imports or very early startup), falls back to the
    static ``_DEFAULTS`` dict.
    """

    if _client is None:
        if flag_name in _overrides:
            return _overrides[flag_name]
        if default is not None:
            return default
        return _DEFAULTS.get(flag_name, False)

    return bool(_client.is_enabled(flag_name, default=default or _DEFAULTS.get(flag_name, False)))


async def shutdown_flags() -> None:
    """Graceful teardown (called from app lifespan)."""

    global _client, _unleash_started
    if _client is not None and _unleash_started:
        with contextlib.suppress(Exception):
            _client.destroy()  # type: ignore[union-attr]
    _client = None
    _unleash_started = False


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def set_override(flag_name: str, value: bool) -> None:
    """Force a flag value — for tests only."""

    _overrides[flag_name] = value


def clear_overrides() -> None:
    """Remove all test overrides."""

    _overrides.clear()


def reset_client_for_tests() -> None:
    """Reset the client singleton (tests)."""

    global _client, _unleash_started
    _client = None
    _unleash_started = False
