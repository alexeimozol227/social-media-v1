"""Unit tests for :class:`app.adapters.llm.PolzaProvider` skeleton (PR #17).

Coverage:

* Provider is constructible when ``api_key`` is set; httpx client
  uses the configured base URL.
* ``complete`` / ``embed`` raise :class:`NotImplementedError`
  pending Sprint 3 wire-up.
* Empty ``api_key`` is rejected at construction time (defensive,
  even though the factory normally guards it).
* ``__repr__`` doesn't leak the api key.
* :func:`build_default_provider` resolves ``llm_provider="mock"``
  to :class:`MockLLMProvider` with the configured ``dim``, and
  ``llm_provider="polza"`` without an api key raises a clear
  :class:`RuntimeError`.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.adapters.llm import MockLLMProvider, PolzaProvider, build_default_provider
from app.core.config import settings


@pytest.fixture
def reset_llm_settings() -> Iterator[None]:
    """Restore the (llm_provider, polza_api_key) settings after each test."""

    saved = (
        settings.llm_provider,
        settings.polza_api_key,
        settings.polza_base_url,
        settings.embedding_dim,
    )
    yield
    (
        settings.llm_provider,
        settings.polza_api_key,
        settings.polza_base_url,
        settings.embedding_dim,
    ) = saved


@pytest.mark.asyncio
async def test_polza_constructible_and_base_url_is_configured() -> None:
    provider = PolzaProvider(
        api_key="tk_test_secret_value",
        base_url="https://api.polza.ai/api/v1/",
    )
    try:
        # ``rstrip`` keeps the base URL canonical.
        assert provider.base_url == "https://api.polza.ai/api/v1"
        # httpx.AsyncClient is wired with the same base + bearer header.
        assert str(provider._client.base_url).rstrip("/") == provider.base_url
        auth_header = provider._client.headers["Authorization"]
        assert auth_header == "Bearer tk_test_secret_value"
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_polza_complete_and_embed_raise_not_implemented() -> None:
    provider = PolzaProvider(api_key="tk_test_secret_value")
    try:
        with pytest.raises(NotImplementedError) as completion_exc:
            await provider.complete("hi", model="gpt-4o-mini")
        assert "sprint 3" in str(completion_exc.value).lower()

        with pytest.raises(NotImplementedError) as embed_exc:
            await provider.embed("hi", model="text-embedding-3-small")
        assert "sprint 3" in str(embed_exc.value).lower()
    finally:
        await provider.aclose()


def test_polza_requires_api_key() -> None:
    with pytest.raises(ValueError) as exc:
        PolzaProvider(api_key="")
    assert "api_key" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_polza_repr_does_not_leak_api_key() -> None:
    secret = "tk_test_super_secret_123456789"
    provider = PolzaProvider(api_key=secret)
    try:
        rendered = repr(provider)
        assert secret not in rendered
        # Operators still get a 4-char correlation suffix.
        assert "6789" in rendered
        assert "base_url=" in rendered
    finally:
        await provider.aclose()


def test_build_default_provider_mock(reset_llm_settings: None) -> None:
    del reset_llm_settings
    settings.llm_provider = "mock"
    settings.embedding_dim = 1536

    provider = build_default_provider()

    assert isinstance(provider, MockLLMProvider)
    assert provider.dim == 1536


@pytest.mark.asyncio
async def test_build_default_provider_polza_with_api_key(
    reset_llm_settings: None,
) -> None:
    del reset_llm_settings
    settings.llm_provider = "polza"
    settings.polza_api_key = "tk_test_xyz"
    settings.polza_base_url = "https://api.polza.ai/api/v1"

    provider = build_default_provider()

    assert isinstance(provider, PolzaProvider)
    try:
        assert provider.base_url == "https://api.polza.ai/api/v1"
    finally:
        await provider.aclose()


def test_build_default_provider_polza_without_api_key_fails(
    reset_llm_settings: None,
) -> None:
    del reset_llm_settings
    settings.llm_provider = "polza"
    settings.polza_api_key = ""

    with pytest.raises(RuntimeError) as exc:
        build_default_provider()
    assert "POLZA_API_KEY" in str(exc.value)
