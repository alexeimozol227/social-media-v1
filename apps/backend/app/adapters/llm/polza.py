"""Polza (https://polza.ai/) LLM provider — production wire-up.

PR #20 / docs/plans/phase1-sprint3-plan.md §2.1.2 — replaces the
PR #17 skeleton with a fully wired HTTP client:

* ``httpx.AsyncClient`` with a per-call ``Idempotency-Key`` header.
* ``tenacity`` jittered exponential backoff (retries
  :class:`LLMTimeoutError`, :class:`LLMRateLimitError`,
  :class:`LLMProviderUnavailableError` only — never
  :class:`LLMContextLengthError` or
  :class:`LLMContentFilterBlockedError`).
* ``pybreaker`` per-(provider, model) circuit breaker with Redis
  storage (state is shared across web + worker processes).
* Optional Redis-backed completion cache keyed on the deterministic
  hash of ``(provider, model, messages, tools, response_format)``.

Streaming is intentionally not implemented for MVP (docs/04
§16.2). Every chat call buffers the full response.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import TypeVar

import httpx
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from app.adapters.llm.base import (
    ChatMessage,
    ChatResponse,
    LLMCircuitBreakerOpenError,
    LLMContentFilterBlockedError,
    LLMContextLengthError,
    LLMError,
    LLMProviderError,
    LLMProviderUnavailableError,
    LLMRateLimitError,
    LLMTimeoutError,
    ProviderHealth,
    ResponseFormat,
    ToolCall,
    ToolSpec,
    Usage,
)
from app.adapters.llm.circuit_breaker import LLMCircuitBreakerRegistry
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


T = TypeVar("T")


def _retryable(exc: BaseException) -> bool:
    """Tenacity predicate — only retry on transient errors."""

    return isinstance(
        exc,
        (LLMTimeoutError, LLMRateLimitError, LLMProviderUnavailableError),
    )


class PolzaProvider:
    """Production-grade HTTP client for the Polza LLM gateway.

    Construction is cheap and side-effect-free; the underlying
    :class:`httpx.AsyncClient` opens its first connection lazily.
    Tests pass ``transport=`` (typically ``MockTransport`` from
    ``respx``) so no real socket is ever opened in CI.
    """

    provider_slug: str = "polza"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.polza.ai/api/v1",
        timeout: float | None = None,
        max_attempts: int | None = None,
        initial_backoff_seconds: float | None = None,
        max_backoff_seconds: float | None = None,
        breaker_registry: LLMCircuitBreakerRegistry | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        cache: PolzaResponseCache | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("PolzaProvider requires a non-empty api_key")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout if timeout is not None else settings.llm_gateway_timeout_seconds
        self._max_attempts = (
            max_attempts if max_attempts is not None else settings.llm_gateway_retry_max_attempts
        )
        self._initial_backoff = (
            initial_backoff_seconds
            if initial_backoff_seconds is not None
            else settings.llm_gateway_retry_initial_backoff_seconds
        )
        self._max_backoff = (
            max_backoff_seconds
            if max_backoff_seconds is not None
            else settings.llm_gateway_retry_max_backoff_seconds
        )
        self._breaker_registry = breaker_registry or LLMCircuitBreakerRegistry()
        self._cache = cache
        client_kwargs: dict[str, object] = {
            "base_url": self._base_url,
            "timeout": self._timeout,
            "headers": {
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "social-media-v1/backend",
            },
        }
        if transport is not None:
            client_kwargs["transport"] = transport
        self._client = httpx.AsyncClient(**client_kwargs)  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # introspection
    # ------------------------------------------------------------------

    @property
    def base_url(self) -> str:
        return self._base_url

    def __repr__(self) -> str:
        suffix = f"...{self._api_key[-4:]}" if len(self._api_key) > 4 else "***"
        return f"<PolzaProvider base_url={self._base_url} key=…{suffix}>"

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # chat
    # ------------------------------------------------------------------

    async def chat(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolSpec] | None = None,
        response_format: ResponseFormat | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2000,
        idempotency_key: str | None = None,
    ) -> ChatResponse:
        if not messages:
            raise LLMProviderError("PolzaProvider.chat: empty messages list")

        cache_key: str | None = None
        if self._cache is not None and self._cache.ttl_seconds > 0:
            cache_key = _make_cache_key(
                provider=self.provider_slug,
                model=model,
                messages=messages,
                tools=tools,
                response_format=response_format,
            )
            cached = await self._cache.get(cache_key)
            if cached is not None:
                return cached

        payload: dict[str, object] = {
            "model": model,
            "messages": [_message_payload(m) for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
                for tool in tools
            ]
        if response_format is not None:
            rf: dict[str, object] = {"type": response_format.type}
            if response_format.type == "json_schema" and response_format.json_schema is not None:
                rf["json_schema"] = response_format.json_schema
            payload["response_format"] = rf

        headers: dict[str, str] = {}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

        async def _call() -> ChatResponse:
            data = await self._post("/chat/completions", payload, headers=headers)
            return _parse_chat_response(data, default_model=model)

        result = await self._with_breaker_and_retry(model=model, call=_call)
        if cache_key is not None and self._cache is not None:
            await self._cache.set(cache_key, result)
        return result

    # ------------------------------------------------------------------
    # embed
    # ------------------------------------------------------------------

    async def embed(
        self,
        texts: list[str],
        model: str = "text-embedding-3-small",
    ) -> list[list[float]]:
        if not texts:
            raise LLMProviderError("PolzaProvider.embed: empty texts list")
        for text in texts:
            if not text:
                raise LLMProviderError("PolzaProvider.embed: empty text in batch")

        payload: dict[str, object] = {"model": model, "input": texts}

        async def _call() -> list[list[float]]:
            data = await self._post("/embeddings", payload)
            return _parse_embeddings_response(data, expected=len(texts))

        return await self._with_breaker_and_retry(model=model, call=_call)

    # ------------------------------------------------------------------
    # health_check
    # ------------------------------------------------------------------

    async def health_check(self) -> ProviderHealth:
        start = perf_counter()
        try:
            # 1-token probe — cheapest valid chat call.
            await self._post(
                "/chat/completions",
                {
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                    "temperature": 0.0,
                },
            )
        except LLMError as exc:
            elapsed_ms = int((perf_counter() - start) * 1000)
            return ProviderHealth(
                provider=self.provider_slug,
                status="down",
                latency_ms=max(0, elapsed_ms),
                error_code=exc.error_code,
                detail=exc.message,
            )
        elapsed_ms = int((perf_counter() - start) * 1000)
        status: str = "ok" if elapsed_ms < 2_000 else "degraded"
        return ProviderHealth(
            provider=self.provider_slug,
            status=status,  # type: ignore[arg-type]
            latency_ms=max(0, elapsed_ms),
        )

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    async def _with_breaker_and_retry(
        self,
        *,
        model: str,
        call: Callable[[], Awaitable[T]],
    ) -> T:
        """Wrap ``call`` in the circuit breaker + tenacity retry budget."""

        breaker = await self._breaker_registry.get(self.provider_slug, model)
        if await breaker.is_open_async():
            raise LLMCircuitBreakerOpenError(
                f"Circuit breaker is OPEN for {self.provider_slug}/{model}",
            )

        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(max(1, self._max_attempts)),
                wait=wait_random_exponential(
                    multiplier=self._initial_backoff,
                    max=self._max_backoff,
                ),
                retry=retry_if_exception_type(
                    (LLMTimeoutError, LLMRateLimitError, LLMProviderUnavailableError),
                ),
                reraise=True,
            ):
                with attempt:
                    try:
                        return await call()
                    except (
                        LLMTimeoutError,
                        LLMRateLimitError,
                        LLMProviderUnavailableError,
                    ) as exc:
                        await breaker.record_failure(exc.error_code)
                        raise
                    except LLMError as exc:
                        # Non-retryable -> don't count against the breaker
                        # (those are permanent semantic errors, not a sign
                        # the provider is unhealthy).
                        if not _retryable(exc):
                            raise
                        await breaker.record_failure(exc.error_code)
                        raise
        except RetryError as exc:  # pragma: no cover - reraise=True path
            raise exc.last_attempt.exception() from exc  # type: ignore[misc]

        # Successful call -> reset breaker counters.
        await breaker.record_success()
        # ``AsyncRetrying`` only returns via the ``yield`` so we
        # never reach this point on success; mypy needs the
        # explicit ``raise``.
        raise LLMProviderError("PolzaProvider retry loop terminated without a result")

    async def _post(
        self,
        path: str,
        payload: dict[str, object],
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        try:
            response = await self._client.post(path, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(f"Polza request timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise LLMProviderUnavailableError(f"Polza request failed: {exc}") from exc

        if response.status_code == 429:
            retry_after = _parse_retry_after(response.headers.get("Retry-After"))
            raise LLMRateLimitError(
                "Polza rate-limited the request",
                status_code=response.status_code,
                retry_after_seconds=retry_after,
            )
        if 500 <= response.status_code < 600:
            raise LLMProviderUnavailableError(
                f"Polza returned {response.status_code}",
                status_code=response.status_code,
            )
        if response.status_code >= 400:
            body = _safe_json(response)
            provider_code = _provider_error_code(body)
            message = _provider_error_message(body) or f"Polza HTTP {response.status_code}"
            if provider_code in {"context_length_exceeded", "string_above_max_length"}:
                raise LLMContextLengthError(
                    message,
                    status_code=response.status_code,
                    provider_code=provider_code,
                )
            if provider_code in {"content_filter", "content_policy_violation"}:
                raise LLMContentFilterBlockedError(
                    message,
                    status_code=response.status_code,
                    provider_code=provider_code,
                )
            raise LLMProviderError(
                message,
                status_code=response.status_code,
                provider_code=provider_code,
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise LLMProviderError("Polza returned non-JSON body") from exc
        if not isinstance(data, dict):
            raise LLMProviderError("Polza response body is not a JSON object")
        return data


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _message_payload(message: ChatMessage) -> dict[str, object]:
    payload: dict[str, object] = {"role": message.role, "content": message.content}
    if message.name:
        payload["name"] = message.name
    if message.tool_call_id:
        payload["tool_call_id"] = message.tool_call_id
    return payload


def _safe_json(response: httpx.Response) -> dict[str, object]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _provider_error_code(body: dict[str, object]) -> str | None:
    err = body.get("error")
    if isinstance(err, dict):
        code = err.get("code")
        if isinstance(code, str):
            return code
        type_field = err.get("type")
        if isinstance(type_field, str):
            return type_field
    return None


def _provider_error_message(body: dict[str, object]) -> str | None:
    err = body.get("error")
    if isinstance(err, dict):
        message = err.get("message")
        if isinstance(message, str):
            return message
    return None


def _parse_retry_after(header: str | None) -> float | None:
    if not header:
        return None
    try:
        return max(0.0, float(header))
    except ValueError:
        return None


def _parse_chat_response(data: dict[str, object], *, default_model: str) -> ChatResponse:
    """Parse an OpenAI-compatible chat-completions payload."""

    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMProviderError("Polza chat response missing 'choices'")
    first = choices[0]
    if not isinstance(first, dict):
        raise LLMProviderError("Polza chat response: 'choices[0]' is not an object")
    message = first.get("message")
    if not isinstance(message, dict):
        raise LLMProviderError("Polza chat response: 'choices[0].message' is not an object")
    content_raw = message.get("content")
    content = content_raw if isinstance(content_raw, str) else ""

    tool_calls: list[ToolCall] = []
    raw_tool_calls = message.get("tool_calls")
    if isinstance(raw_tool_calls, list):
        for tc in raw_tool_calls:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function")
            if not isinstance(fn, dict):
                continue
            name = fn.get("name")
            arguments = fn.get("arguments")
            tool_id = tc.get("id")
            if not isinstance(name, str) or not isinstance(arguments, str):
                continue
            tool_calls.append(
                ToolCall(
                    id=tool_id if isinstance(tool_id, str) else f"call_{len(tool_calls)}",
                    name=name,
                    arguments_json=arguments,
                ),
            )

    finish_raw = first.get("finish_reason")
    finish_reason: str = finish_raw if isinstance(finish_raw, str) else "stop"
    if finish_reason not in ("stop", "length", "tool_calls", "content_filter"):
        finish_reason = "stop"

    usage_raw = data.get("usage")
    usage = Usage()
    if isinstance(usage_raw, dict):
        usage = Usage(
            prompt_tokens=_int_field(usage_raw, "prompt_tokens"),
            completion_tokens=_int_field(usage_raw, "completion_tokens"),
            total_tokens=_int_field(usage_raw, "total_tokens"),
        )

    model_raw = data.get("model")
    response_id = data.get("id")
    return ChatResponse(
        content=content,
        tool_calls=tool_calls,
        finish_reason=finish_reason,  # type: ignore[arg-type]
        model=model_raw if isinstance(model_raw, str) else default_model,
        usage=usage,
        response_id=response_id if isinstance(response_id, str) else None,
    )


def _int_field(body: dict[str, object], key: str) -> int:
    value = body.get(key)
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value))
    return 0


def _parse_embeddings_response(data: dict[str, object], *, expected: int) -> list[list[float]]:
    raw = data.get("data")
    if not isinstance(raw, list):
        raise LLMProviderError("Polza embeddings response missing 'data' array")
    if len(raw) != expected:
        raise LLMProviderError(
            f"Polza embeddings response length {len(raw)} != expected {expected}",
        )
    vectors: list[list[float]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise LLMProviderError("Polza embeddings response entry is not an object")
        vec = entry.get("embedding")
        if not isinstance(vec, list):
            raise LLMProviderError("Polza embeddings response entry missing 'embedding' list")
        floats: list[float] = []
        for v in vec:
            if isinstance(v, bool):
                raise LLMProviderError("Polza embeddings vector contains bool")
            if isinstance(v, (int, float)):
                floats.append(float(v))
            else:
                raise LLMProviderError("Polza embeddings vector contains non-numeric")
        vectors.append(floats)
    return vectors


# ---------------------------------------------------------------------------
# Optional Redis-backed prompt cache
# ---------------------------------------------------------------------------


class PolzaResponseCache:
    """Thin wrapper around an async Redis client storing ChatResponse JSON.

    Decoupled from :class:`PolzaProvider` so tests pass a fakeredis
    client and the production path uses the shared
    :func:`app.core.redis.get_redis` singleton.
    """

    def __init__(
        self, *, redis_client: object, ttl_seconds: int, namespace: str = "llm-cache"
    ) -> None:
        self._redis = redis_client
        self._ttl = max(0, int(ttl_seconds))
        self._namespace = namespace

    @property
    def ttl_seconds(self) -> int:
        return self._ttl

    def _key(self, payload_hash: str) -> str:
        return f"{self._namespace}:{payload_hash}"

    async def get(self, payload_hash: str) -> ChatResponse | None:
        get = self._redis.get  # type: ignore[attr-defined]
        raw_obj = await _maybe_await(get(self._key(payload_hash)))
        if raw_obj is None:
            return None
        if isinstance(raw_obj, (bytes, bytearray)):
            raw_str = raw_obj.decode("utf-8")
        elif isinstance(raw_obj, str):
            raw_str = raw_obj
        else:  # pragma: no cover - unexpected backend payload
            return None
        try:
            return ChatResponse.model_validate_json(raw_str)
        except Exception:  # pragma: no cover - corrupted cache entry
            logger.warning("polza.cache.decode_failed")
            return None

    async def set(self, payload_hash: str, response: ChatResponse) -> None:
        if self._ttl <= 0:
            return
        set_ = self._redis.set  # type: ignore[attr-defined]
        await _maybe_await(
            set_(
                self._key(payload_hash),
                response.model_dump_json(),
                ex=self._ttl,
            ),
        )


async def _maybe_await(value: object) -> object:
    if asyncio.iscoroutine(value):
        return await value
    return value


def _make_cache_key(
    *,
    provider: str,
    model: str,
    messages: list[ChatMessage],
    tools: list[ToolSpec] | None,
    response_format: ResponseFormat | None,
) -> str:
    """Deterministic SHA-256 over the canonical payload."""

    canonical: dict[str, object] = {
        "provider": provider,
        "model": model,
        "messages": [
            {
                "role": m.role,
                "content": m.content,
                "name": m.name,
                "tool_call_id": m.tool_call_id,
            }
            for m in messages
        ],
        "tools": [
            {"name": t.name, "description": t.description, "parameters": t.parameters}
            for t in (tools or [])
        ],
        "response_format": response_format.model_dump() if response_format else None,
    }
    blob = json.dumps(canonical, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


__all__ = [
    "PolzaProvider",
    "PolzaResponseCache",
]
