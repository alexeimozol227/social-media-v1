"""Unit tests for :class:`app.adapters.llm.PolzaProvider` (PR #20).

Coverage:

* Constructor wires bearer header + base URL canonicalisation.
* Empty ``api_key`` is rejected.
* ``__repr__`` doesn't leak the api key.
* Happy-path chat call hits the OpenAI-style ``/chat/completions``
  endpoint and parses the response.
* ``embed`` parses batched embedding payloads.
* ``429`` surfaces as :class:`LLMRateLimitError`; ``5xx`` surfaces
  as :class:`LLMProviderUnavailableError` and counts against the
  in-process breaker registry; non-retryable codes surface as
  :class:`LLMContextLengthError` / :class:`LLMContentFilterBlockedError`.
* The retry decorator burns the configured budget on 5xx then
  flips the breaker into ``OPEN``.
* ``health_check`` returns ``ok`` on success and ``down`` on error.
* :func:`build_default_provider` resolves ``llm_provider="mock"`` →
  :class:`MockLLMProvider`, and ``llm_provider="polza"`` without
  an API key raises a clear :class:`RuntimeError`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
import respx

from app.adapters.llm import (
    ChatMessage,
    LLMCircuitBreakerOpenError,
    LLMContentFilterBlockedError,
    LLMContextLengthError,
    LLMProviderUnavailableError,
    LLMRateLimitError,
    LLMTimeoutError,
    MockLLMProvider,
    PolzaProvider,
    build_default_provider,
)
from app.adapters.llm.circuit_breaker import (
    CircuitBreakerConfig,
    LLMCircuitBreakerRegistry,
)
from app.core.config import settings


@pytest.fixture
def reset_llm_settings() -> Iterator[None]:
    saved = (
        settings.llm_provider,
        settings.polza_api_key,
        settings.polza_base_url,
        settings.embedding_dim,
        settings.llm_prompt_cache_ttl_seconds,
    )
    yield
    (
        settings.llm_provider,
        settings.polza_api_key,
        settings.polza_base_url,
        settings.embedding_dim,
        settings.llm_prompt_cache_ttl_seconds,
    ) = saved


@pytest.fixture
async def polza() -> AsyncIterator[PolzaProvider]:
    provider = PolzaProvider(
        api_key="tk_test_secret",
        base_url="https://api.polza.ai/api/v1",
        max_attempts=1,  # disable retries by default; individual tests opt in
        initial_backoff_seconds=0.0,
        max_backoff_seconds=0.0,
    )
    try:
        yield provider
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_polza_constructible_and_base_url_is_configured() -> None:
    provider = PolzaProvider(
        api_key="tk_test_secret_value",
        base_url="https://api.polza.ai/api/v1/",
    )
    try:
        assert provider.base_url == "https://api.polza.ai/api/v1"
        assert str(provider._client.base_url).rstrip("/") == provider.base_url
        assert provider._client.headers["Authorization"] == "Bearer tk_test_secret_value"
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
        assert "6789" in rendered
        assert "base_url=" in rendered
    finally:
        await provider.aclose()


@respx.mock
@pytest.mark.asyncio
async def test_chat_happy_path_parses_response(polza: PolzaProvider) -> None:
    route = respx.post("https://api.polza.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "resp_abc",
                "model": "gpt-4o-mini",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "hi"},
                        "finish_reason": "stop",
                    },
                ],
                "usage": {
                    "prompt_tokens": 3,
                    "completion_tokens": 2,
                    "total_tokens": 5,
                },
            },
        ),
    )
    result = await polza.chat(
        [ChatMessage(role="user", content="hello")],
        model="gpt-4o-mini",
        idempotency_key="idem-1",
    )
    assert route.called
    sent = route.calls[0].request
    assert sent.headers["Idempotency-Key"] == "idem-1"
    assert result.content == "hi"
    assert result.usage.total_tokens == 5
    assert result.response_id == "resp_abc"


@respx.mock
@pytest.mark.asyncio
async def test_chat_429_surfaces_rate_limit_error(polza: PolzaProvider) -> None:
    respx.post("https://api.polza.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(
            429,
            json={"error": {"message": "Too many requests", "code": "rate_limited"}},
            headers={"Retry-After": "2.5"},
        ),
    )
    with pytest.raises(LLMRateLimitError) as exc:
        await polza.chat(
            [ChatMessage(role="user", content="hi")],
            model="gpt-4o-mini",
        )
    assert exc.value.retry_after_seconds == pytest.approx(2.5)


@respx.mock
@pytest.mark.asyncio
async def test_chat_5xx_surfaces_provider_unavailable(polza: PolzaProvider) -> None:
    respx.post("https://api.polza.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(503, json={}),
    )
    with pytest.raises(LLMProviderUnavailableError):
        await polza.chat(
            [ChatMessage(role="user", content="hi")],
            model="gpt-4o-mini",
        )


@respx.mock
@pytest.mark.asyncio
async def test_chat_context_length_surfaces_typed_error(polza: PolzaProvider) -> None:
    respx.post("https://api.polza.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(
            400,
            json={
                "error": {
                    "message": "Context too long",
                    "code": "context_length_exceeded",
                },
            },
        ),
    )
    with pytest.raises(LLMContextLengthError):
        await polza.chat(
            [ChatMessage(role="user", content="x" * 1024)],
            model="gpt-4o-mini",
        )


@respx.mock
@pytest.mark.asyncio
async def test_chat_content_filter_surfaces_typed_error(polza: PolzaProvider) -> None:
    respx.post("https://api.polza.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(
            400,
            json={
                "error": {
                    "message": "Filtered",
                    "code": "content_filter",
                },
            },
        ),
    )
    with pytest.raises(LLMContentFilterBlockedError):
        await polza.chat(
            [ChatMessage(role="user", content="forbidden")],
            model="gpt-4o-mini",
        )


@respx.mock
@pytest.mark.asyncio
async def test_chat_timeouts_are_classified() -> None:
    respx.post("https://api.polza.ai/api/v1/chat/completions").mock(
        side_effect=httpx.ReadTimeout("simulated"),
    )
    provider = PolzaProvider(
        api_key="tk_test",
        base_url="https://api.polza.ai/api/v1",
        max_attempts=1,
        initial_backoff_seconds=0.0,
        max_backoff_seconds=0.0,
    )
    try:
        with pytest.raises(LLMTimeoutError):
            await provider.chat(
                [ChatMessage(role="user", content="hi")],
                model="gpt-4o-mini",
            )
    finally:
        await provider.aclose()


@respx.mock
@pytest.mark.asyncio
async def test_chat_retries_then_opens_breaker() -> None:
    """5xx is retried up to ``max_attempts``; once exhausted the
    breaker opens and the next call short-circuits with
    :class:`LLMCircuitBreakerOpenError` without touching the
    network."""

    route = respx.post("https://api.polza.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(502, json={}),
    )
    registry = LLMCircuitBreakerRegistry(
        config=CircuitBreakerConfig(fail_threshold=1, reset_seconds=60),
    )
    provider = PolzaProvider(
        api_key="tk_test",
        base_url="https://api.polza.ai/api/v1",
        max_attempts=2,
        initial_backoff_seconds=0.0,
        max_backoff_seconds=0.0,
        breaker_registry=registry,
    )
    try:
        with pytest.raises(LLMProviderUnavailableError):
            await provider.chat(
                [ChatMessage(role="user", content="hi")],
                model="gpt-4o-mini",
            )
        # ``max_attempts=2`` exhausted -> breaker OPEN.
        breaker = await registry.get("polza", "gpt-4o-mini")
        assert await breaker.is_open_async() is True

        # Next call short-circuits without touching the network.
        baseline_calls = route.call_count
        with pytest.raises(LLMCircuitBreakerOpenError):
            await provider.chat(
                [ChatMessage(role="user", content="hi")],
                model="gpt-4o-mini",
            )
        assert route.call_count == baseline_calls
    finally:
        await provider.aclose()


@respx.mock
@pytest.mark.asyncio
async def test_embed_parses_batched_response(polza: PolzaProvider) -> None:
    respx.post("https://api.polza.ai/api/v1/embeddings").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"embedding": [0.1, 0.2], "index": 0},
                    {"embedding": [0.3, 0.4], "index": 1},
                ],
                "model": "text-embedding-3-small",
            },
        ),
    )
    result = await polza.embed(["a", "b"], model="text-embedding-3-small")
    assert result == [[0.1, 0.2], [0.3, 0.4]]


@respx.mock
@pytest.mark.asyncio
async def test_health_check_ok(polza: PolzaProvider) -> None:
    respx.post("https://api.polza.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "x",
                "model": "gpt-4o-mini",
                "choices": [
                    {"message": {"role": "assistant", "content": "pong"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        ),
    )
    health = await polza.health_check()
    assert health.provider == "polza"
    assert health.status in ("ok", "degraded")


@respx.mock
@pytest.mark.asyncio
async def test_health_check_down_on_5xx(polza: PolzaProvider) -> None:
    respx.post("https://api.polza.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(500, json={}),
    )
    health = await polza.health_check()
    assert health.status == "down"
    assert health.error_code == "LLM_PROVIDER_UNAVAILABLE"


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
    settings.llm_prompt_cache_ttl_seconds = 0

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
