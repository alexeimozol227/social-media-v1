"""Feature flag wrapper + ``GET /v1/feature-flags`` (PR #8).

Covers:

* The wrapper's safe-fallback path: with ``UNLEASH_URL`` unset, every
  ``is_enabled`` call returns the supplied default and never touches
  the network or imports the upstream client.
* The named flag constant is the canonical name from docs/07 §3
  (``auto_publish_enabled``) — not the working-name from the plan.
* The default for ``auto_publish_enabled`` is ``False`` (fail-closed
  on automation per docs/04 §12.2 + docs/07 §3).
* The REST endpoint shape: ``GET /v1/feature-flags`` returns one key
  per resolved flag, no extras.
* When Unleash is unavailable and the upstream client raises, the
  wrapper degrades gracefully to the default.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from app.core.feature_flags import (
    FLAG_AUTO_PUBLISH_ENABLED,
    FeatureFlagClient,
    is_enabled,
)


def test_flag_name_matches_docs_canonical() -> None:
    """docs/07 §3 names the trial-expiry switch ``auto_publish_enabled``."""

    assert FLAG_AUTO_PUBLISH_ENABLED == "auto_publish_enabled"


def test_safe_fallback_when_unleash_disabled() -> None:
    """Empty ``unleash_url`` → ``is_enabled`` returns ``default``.

    No upstream client should be instantiated; we assert by inspecting
    the wrapper's private state after the call.
    """

    client = FeatureFlagClient()
    assert client.is_enabled(FLAG_AUTO_PUBLISH_ENABLED, default=False) is False
    assert client.is_enabled(FLAG_AUTO_PUBLISH_ENABLED, default=True) is True
    # No upstream client was created.
    assert client._client is None
    assert client._init_attempted is True


def test_module_helper_uses_singleton_default() -> None:
    """The module-level :func:`is_enabled` honours the supplied default."""

    assert is_enabled(FLAG_AUTO_PUBLISH_ENABLED, default=False) is False
    assert is_enabled(FLAG_AUTO_PUBLISH_ENABLED, default=True) is True


def test_wrapper_swallows_upstream_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    """A misbehaving upstream client must not surface to the caller."""

    class _BoomClient:
        def is_enabled(
            self,
            feature_name: str,
            context: dict[str, Any] | None = None,
            fallback_function: Any = None,
        ) -> bool:
            raise RuntimeError("upstream exploded")

        def destroy(self) -> None:
            pass

    client = FeatureFlagClient()
    # Skip initialise; inject the fake directly.
    client._init_attempted = True
    client._client = _BoomClient()

    assert client.is_enabled("any_flag", default=False) is False
    assert client.is_enabled("any_flag", default=True) is True


def test_wrapper_uses_upstream_value_when_available() -> None:
    """When the upstream client is reachable, we trust its verdict."""

    class _StaticClient:
        def __init__(self, value: bool) -> None:
            self._value = value
            self.calls: list[str] = []

        def is_enabled(
            self,
            feature_name: str,
            context: dict[str, Any] | None = None,
            fallback_function: Any = None,
        ) -> bool:
            self.calls.append(feature_name)
            return self._value

        def destroy(self) -> None:
            pass

    client = FeatureFlagClient()
    client._init_attempted = True
    fake = _StaticClient(value=True)
    client._client = fake

    # Default is ignored when the upstream returns a value.
    assert client.is_enabled("any_flag", default=False) is True
    assert fake.calls == ["any_flag"]


async def test_feature_flags_endpoint_returns_defaults(client: AsyncClient) -> None:
    """Anonymous caller sees the safe defaults (all False)."""

    r = await client.get("/v1/feature-flags")
    assert r.status_code == 200
    payload = r.json()
    assert payload == {"auto_publish_enabled": False}
