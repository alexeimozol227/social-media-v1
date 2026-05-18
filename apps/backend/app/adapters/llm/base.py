"""LLMProvider Protocol + Pydantic types + typed errors.

PR #20 / docs/plans/phase1-sprint3-plan.md — replaces the PR #17
skeleton (``complete()`` + ``embed(text)``) with the canonical
contract every agent and embedding pipeline depends on:

* :meth:`LLMProvider.chat` — chat completions (system / user /
  assistant turns) with optional tool calling + ``response_format``
  / json-schema. Streaming is intentionally out-of-scope for MVP
  (docs/04 §16.2) — the response is buffered in full.
* :meth:`LLMProvider.embed` — *batched* embedding. Single-text
  callers wrap in ``[text]`` and read ``result[0]``.
* :meth:`LLMProvider.health_check` — non-budget-charging probe used
  by ``HealthCheckAgent`` and the ``/v1/admin/healthcheck/llm``
  endpoint.

Every public surface uses Pydantic models — ``dict[str, Any]`` is
banned in inter-agent contracts per docs/04-architecture.md
П6 / D34 ("Strict typing everywhere").

Typed errors (all subclass :class:`LLMError`):

* :class:`LLMTimeoutError` — HTTP / socket timeout. Retryable.
* :class:`LLMRateLimitError` — provider 429. Retryable, with
  jittered backoff.
* :class:`LLMProviderUnavailableError` — provider 5xx (502 / 503 /
  504). Retryable; counts against the circuit breaker.
* :class:`LLMBudgetExceededError` — workspace budget is depleted
  (D60, docs/04 §18.4). Never retried.
* :class:`LLMContextLengthError` — prompt exceeded the model's
  context window. Never retried (caller must trim).
* :class:`LLMContentFilterBlockedError` — provider's safety filter
  rejected the prompt or response. Never retried.
* :class:`LLMCircuitBreakerOpenError` — the per-(provider, model)
  breaker is OPEN; fail fast without consuming the retry budget.
* :class:`LLMProviderError` — catch-all for everything else (auth,
  malformed payload, unknown model). Never retried.
"""

from __future__ import annotations

from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Typed errors
# ---------------------------------------------------------------------------


class LLMError(Exception):
    """Base class for every error raised by an :class:`LLMProvider`."""

    error_code: str = "LLM_ERROR"

    def __init__(
        self,
        message: str | None = None,
        *,
        status_code: int | None = None,
        provider_code: str | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message or self.__class__.__name__)
        self.message = message or self.__class__.__name__
        self.status_code = status_code
        self.provider_code = provider_code
        self.retry_after_seconds = retry_after_seconds


class LLMTimeoutError(LLMError):
    """HTTP / connection timeout. Retryable."""

    error_code = "LLM_TIMEOUT"


class LLMRateLimitError(LLMError):
    """Provider returned ``429``. Retryable with jittered backoff."""

    error_code = "LLM_RATE_LIMITED"


class LLMProviderUnavailableError(LLMError):
    """Provider returned ``5xx``. Retryable; counts against the breaker."""

    error_code = "LLM_PROVIDER_UNAVAILABLE"


class LLMBudgetExceededError(LLMError):
    """Workspace burnt through its budget (D60, docs/04 §18.4)."""

    error_code = "LLM_BUDGET_EXCEEDED"


class LLMContextLengthError(LLMError):
    """Prompt exceeded the model's context window. Permanent."""

    error_code = "LLM_CONTEXT_LENGTH_EXCEEDED"


class LLMContentFilterBlockedError(LLMError):
    """Content filter blocked the prompt / response. Permanent."""

    error_code = "LLM_CONTENT_FILTER_BLOCKED"


class LLMCircuitBreakerOpenError(LLMError):
    """Per-(provider, model) circuit breaker is OPEN — fail fast.

    Raised by the breaker wrapper without invoking the provider,
    so the retry budget isn't burned on a known-bad endpoint.
    """

    error_code = "LLM_CIRCUIT_BREAKER_OPEN"


class LLMProviderError(LLMError):
    """Catch-all for "the request reached the provider but it said no"."""

    error_code = "LLM_PROVIDER_ERROR"


# ---------------------------------------------------------------------------
# Pydantic message / tool / response types
# ---------------------------------------------------------------------------


class ChatMessage(BaseModel):
    """One turn in a chat-completion exchange.

    Mirrors the OpenAI ``messages`` shape so every OpenAI-compatible
    gateway (Polza, OpenRouter, …) accepts it unchanged.
    """

    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant", "tool"]
    content: str
    name: str | None = None
    tool_call_id: str | None = None


class ToolSpec(BaseModel):
    """OpenAI-style function / tool schema fed into :meth:`LLMProvider.chat`.

    ``parameters`` is a JSON Schema object — typed as
    ``dict[str, object]`` so the contract is explicitly typed (D34
    bans ``Any`` in inter-agent contracts).
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=64)
    description: str = Field(..., min_length=1)
    parameters: dict[str, object] = Field(default_factory=dict)


class ToolCall(BaseModel):
    """A single tool invocation emitted by the assistant."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    arguments_json: str = Field(
        ...,
        description=(
            "JSON-serialised arguments. Kept as a string so the agent "
            "layer chooses when to parse / validate against the matching "
            "ToolSpec.parameters schema."
        ),
    )


class Usage(BaseModel):
    """Token usage breakdown returned by the provider."""

    model_config = ConfigDict(extra="forbid")

    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)


ResponseFormatT = Literal["text", "json_object", "json_schema"]


class ResponseFormat(BaseModel):
    """``response_format`` payload — controls structured output."""

    model_config = ConfigDict(extra="forbid")

    type: ResponseFormatT = "text"
    json_schema: dict[str, object] | None = None


class ChatResponse(BaseModel):
    """Result of a :meth:`LLMProvider.chat` call."""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(
        default="",
        description="Final assistant text (may be empty when tools fire).",
    )
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: Literal["stop", "length", "tool_calls", "content_filter"] = "stop"
    model: str = Field(default="")
    usage: Usage = Field(default_factory=Usage)
    response_id: str | None = Field(
        default=None,
        description="Provider-side correlation id (echoed into the audit log row).",
    )


class ProviderHealth(BaseModel):
    """:meth:`LLMProvider.health_check` reply."""

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(..., description="Provider slug — ``polza``, ``mock``, …")
    status: Literal["ok", "degraded", "down"] = "ok"
    latency_ms: Annotated[int, Field(ge=0)] = 0
    error_code: str | None = None
    detail: str | None = None


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class LLMProvider(Protocol):
    """The shared LLM surface every agent depends on.

    docs/04 §16: every concrete provider (Polza, Mock, …)
    implements this Protocol. The agent layer never imports a
    concrete class — it accepts an :class:`LLMProvider` parameter
    and lets the factory + DI choose the implementation.
    """

    provider_slug: str
    """Identifier used in the audit log + circuit-breaker key (e.g. ``polza``)."""

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
        """Run one buffered chat-completion exchange.

        ``idempotency_key`` is forwarded as an ``Idempotency-Key``
        header so a retry against the same key returns the same
        response (provider-side dedup).
        """

        ...  # pragma: no cover - Protocol stub

    async def embed(
        self,
        texts: list[str],
        model: str = "text-embedding-3-small",
    ) -> list[list[float]]:
        """Embed a batch of texts into fixed-dim vectors."""

        ...  # pragma: no cover - Protocol stub

    async def health_check(self) -> ProviderHealth:
        """Probe the gateway with a no-cost / minimal-cost ping."""

        ...  # pragma: no cover - Protocol stub


__all__ = [
    "ChatMessage",
    "ChatResponse",
    "LLMBudgetExceededError",
    "LLMCircuitBreakerOpenError",
    "LLMContentFilterBlockedError",
    "LLMContextLengthError",
    "LLMError",
    "LLMProvider",
    "LLMProviderError",
    "LLMProviderUnavailableError",
    "LLMRateLimitError",
    "LLMTimeoutError",
    "ProviderHealth",
    "ResponseFormat",
    "ResponseFormatT",
    "ToolCall",
    "ToolSpec",
    "Usage",
]
