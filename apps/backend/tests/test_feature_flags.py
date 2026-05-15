"""Tests for the feature-flag module (PR #8, D42 + П10).

The test suite never hits a real Unleash server — it exercises the
in-memory fallback client, overrides, and the ``require_feature``
dependency.
"""

from __future__ import annotations

import pytest

from app.core.feature_flags import (
    ENABLE_AUTO_PUBLISH,
    _InMemoryFlagClient,
    clear_overrides,
    is_enabled,
    reset_client_for_tests,
    set_override,
)


@pytest.fixture(autouse=True)
def _clean_flags() -> None:
    """Reset global state before every test."""
    clear_overrides()
    reset_client_for_tests()


# ---- InMemoryFlagClient unit tests ----


class TestInMemoryFlagClient:
    def test_returns_default_false_for_auto_publish(self) -> None:
        client = _InMemoryFlagClient()
        assert client.is_enabled(ENABLE_AUTO_PUBLISH) is False

    def test_respects_explicit_default(self) -> None:
        client = _InMemoryFlagClient()
        assert client.is_enabled("unknown_flag", default=True) is True
        assert client.is_enabled("unknown_flag", default=False) is False

    def test_unknown_flag_defaults_to_false(self) -> None:
        client = _InMemoryFlagClient()
        assert client.is_enabled("totally_unknown") is False


# ---- Module-level ``is_enabled()`` (synchronous helper) ----


class TestIsEnabled:
    def test_auto_publish_off_by_default(self) -> None:
        assert is_enabled(ENABLE_AUTO_PUBLISH) is False

    def test_override_turns_flag_on(self) -> None:
        set_override(ENABLE_AUTO_PUBLISH, True)
        assert is_enabled(ENABLE_AUTO_PUBLISH) is True

    def test_override_turns_flag_off(self) -> None:
        set_override(ENABLE_AUTO_PUBLISH, False)
        assert is_enabled(ENABLE_AUTO_PUBLISH) is False

    def test_clear_overrides_restores_default(self) -> None:
        set_override(ENABLE_AUTO_PUBLISH, True)
        clear_overrides()
        assert is_enabled(ENABLE_AUTO_PUBLISH) is False

    def test_explicit_default_kwarg(self) -> None:
        assert is_enabled("some_flag", default=True) is True


# ---- ``require_feature`` dependency ----


class TestRequireFeatureDependency:
    def test_raises_when_flag_disabled(self) -> None:
        from app.api.deps import require_feature
        from app.errors import FeatureDisabledError

        dep = require_feature(ENABLE_AUTO_PUBLISH)
        with pytest.raises(FeatureDisabledError):
            dep()

    def test_passes_when_flag_enabled(self) -> None:
        from app.api.deps import require_feature

        set_override(ENABLE_AUTO_PUBLISH, True)
        dep = require_feature(ENABLE_AUTO_PUBLISH)
        dep()  # should not raise
