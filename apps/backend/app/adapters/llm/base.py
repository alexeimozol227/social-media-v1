"""LLMProvider Protocol + shared dataclasses (PR #17).

docs/04-architecture.md §16 + docs/05-tech-stack.md §6 fix the
contract every concrete LLM adapter has to satisfy:

* :meth:`LLMProvider.complete` — text completion / chat with optional
  tool calling. The reply carries usage so the budget tracker (D60
  in docs/04 §18.4) can charge the right workspace.
* :meth:`LLMProvider.embed` — vector embedding of arbitrary text.
  Used by the Brand Memory pipeline and the channel-post embedding
  job introduced in PR #17.

The Protocol is :class:`~typing.Protocol`-typed rather than an ABC so
agents can accept a duck-typed test double without inheriting from
the production class. Mypy still verifies the surface — see
:func:`app.adapters.llm.build_default_provider` for the runtime
dispatch.

PR #17 surfaces four typed errors so the Celery retry logic can
discriminate transient failures (retry) from permanent ones
(don't retry, surface the audit event):

* :class:`LLMError` — base class, never raised directly.
* :class:`LLMTimeoutError` — HTTP timeout / connection refused →
  worker re-queues the job with exponential backoff.
* :class:`LLMBudgetExceededError` — workspace burnt through its
  budget; the agent stops, the user is notified via the per-user
  event channel.
* :class:`LLMProviderError` — everything else (auth failure, schema
  mismatch, model not found). The worker logs + drops.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

# ---------------------------------------------------------------------------
# Typed errors
# ---------------------------------------------------------------------------


class LLMError(Exception):
    """Base class for every error raised by an :class:`LLMProvider`.

    Subclasses pin a stable :attr:`error_code` so the Celery retry
    decorator + audit-event writer can route on the code without
    matching exception types. The constructor accepts an optional
    HTTP status / provider-side error-code for richer log lines —
    both default to ``None`` because the mock provider doesn't have
    them.
    """

    error_code: str = "LLM_ERROR"

    def __init__(
        self,
        message: str | None = None,
        *,
        status_code: int | None = None,
        provider_code: str | None = None,
    ) -> None:
        super().__init__(message or self.__class__.__name__)
        self.message = message or self.__class__.__name__
        self.status_code = status_code
        self.provider_code = provider_code


class LLMTimeoutError(LLMError):
    """The provider didn't reply within the configured timeout.

    The Celery task retries with exponential backoff (PR #17 caps
    retries at 3). A transient blip — provider scaling out, network
    weather — clears within one retry; permanent issues surface as
    :class:`LLMProviderError` once the backoff budget is spent.
    """

    error_code = "LLM_TIMEOUT"


class LLMBudgetExceededError(LLMError):
    """The workspace burnt through its budget (D60 in docs/04 §18.4).

    Never retried — the cost guardrail is hard. The agent surfaces
    a typed event so the dashboard can render an upgrade CTA.
    Sprint 3 wires the actual accounting; PR #17 reserves the code
    so the Celery retry logic short-circuits correctly today.
    """

    error_code = "LLM_BUDGET_EXCEEDED"


class LLMProviderError(LLMError):
    """The provider returned an error response (4xx / 5xx).

    Catch-all for "the request reached the provider but it said
    no": auth failure, malformed payload, model not found, content
    policy violation. Not retried — the body of the request hasn't
    changed between retries.
    """

    error_code = "LLM_PROVIDER_ERROR"


# ---------------------------------------------------------------------------
# Result envelopes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Tool:
    """OpenAI-style tool / function schema fed into :meth:`LLMProvider.complete`.

    Mirrors the wire shape so the Polza adapter can forward the
    payload as-is. The Mock provider ignores ``parameters`` — its
    tool-call replies are scripted via the fixtures dict.
    """

    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LLMResult:
    """Result of a :meth:`LLMProvider.complete` call.

    ``text`` holds the final assistant message. ``tool_calls`` is a
    list of ``{"name": ..., "arguments": {...}}`` dicts when the
    provider chose to call a tool instead of (or in addition to)
    emitting text — the agent layer dispatches them. ``usage`` is
    the canonical provider usage payload (``prompt_tokens`` /
    ``completion_tokens`` / ``total_tokens``) used by the cost
    tracker.
    """

    text: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    model: str = ""


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    """Result of a :meth:`LLMProvider.embed` call.

    ``vector`` is the embedding as a list of floats (so callers
    don't need numpy). ``model`` echoes back the model id the
    provider used; agents persist it alongside the vector so a
    later re-embedding pass can detect a model-version bump.
    """

    vector: list[float]
    model: str
    usage: dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class LLMProvider(Protocol):
    """The shared LLM surface every agent depends on.

    docs/04 §16: every concrete provider (Polza, OpenAI-direct,
    Anthropic-direct, mock) implements this Protocol. The agent
    layer never imports a concrete class — it accepts an
    :class:`LLMProvider` parameter and lets dependency injection
    swap the implementation.
    """

    async def complete(
        self,
        prompt: str,
        model: str,
        *,
        tools: list[Tool] | None = None,
        max_tokens: int = 2000,
    ) -> LLMResult:
        """Run a text completion / chat exchange.

        ``prompt`` is the full user-facing prompt (the agent layer
        is responsible for assembling system / user / assistant
        turns). ``tools`` enables function calling — the provider
        is free to ignore it if the model doesn't support tools.
        ``max_tokens`` caps the response so a runaway model can't
        burn the workspace budget.
        """

        ...  # pragma: no cover - Protocol stub

    async def embed(self, text: str, model: str) -> EmbeddingResult:
        """Embed ``text`` into a fixed-dimensionality vector.

        The dimensionality is determined by ``model`` — callers
        validate it against their persistence layer (see
        :class:`app.services.embeddings.EmbeddingsService`).
        """

        ...  # pragma: no cover - Protocol stub


__all__ = [
    "EmbeddingResult",
    "LLMBudgetExceededError",
    "LLMError",
    "LLMProvider",
    "LLMProviderError",
    "LLMResult",
    "LLMTimeoutError",
    "Tool",
]
