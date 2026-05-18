"""Deterministic mock LLM provider for tests + dev.

PR #20 supersedes the PR #17 skeleton: the mock now mirrors the
new :class:`LLMProvider` Protocol (``chat`` + batched ``embed`` +
``health_check``) and ships with fixture lookup keyed on the
last user message (or the concatenated content for tool-call
tests).

Design goals (unchanged from PR #17):

1. **Determinism.** ``embed`` is a pure function of
   ``(model, text, dim)`` so two CI runs produce byte-identical
   vectors.
2. **No real network / no big dependencies.** Stock Python only.
3. **Composable fixtures.** Tests construct
   :class:`MockLLMProvider` inline with
   ``chat_fixtures={"hello": ChatResponse(...)}`` and the mock
   serves the scripted reply for any matching prompt.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field
from time import perf_counter

from app.adapters.llm.base import (
    ChatMessage,
    ChatResponse,
    LLMProvider,
    LLMProviderError,
    ProviderHealth,
    ResponseFormat,
    ToolSpec,
    Usage,
)
from app.models.channel_post_embedding import EMBEDDING_DIM as DEFAULT_DIM


@dataclass
class MockLLMProvider(LLMProvider):
    """Test double that mimics the production provider surface.

    The class is a :func:`~dataclasses.dataclass` so tests can
    construct it inline (``MockLLMProvider(dim=4, …)``) and swap
    fixtures via ``embedding_fixtures`` / ``chat_fixtures``.
    """

    dim: int = DEFAULT_DIM
    """Embedding dimensionality."""

    embedding_fixtures: dict[str, list[float]] = field(default_factory=dict)
    """``text -> vector`` overrides."""

    chat_fixtures: dict[str, ChatResponse] = field(default_factory=dict)
    """``last_user_content -> ChatResponse`` overrides."""

    health_status: str = "ok"
    """Toggle for health-check tests (``ok`` | ``degraded`` | ``down``)."""

    provider_slug: str = "mock"

    def __post_init__(self) -> None:
        if self.dim <= 0:
            raise ValueError(f"MockLLMProvider.dim must be positive, got {self.dim}")

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
        """Return a scripted reply or a deterministic stub."""

        del tools, response_format, temperature, max_tokens, idempotency_key
        if not messages:
            raise LLMProviderError("MockLLMProvider.chat: empty messages list")
        last_user = next(
            (msg.content for msg in reversed(messages) if msg.role == "user"),
            messages[-1].content,
        )
        if last_user in self.chat_fixtures:
            scripted = self.chat_fixtures[last_user]
            return scripted.model_copy(update={"model": scripted.model or model})

        # Default stub: echo the last user message + a token-aware
        # usage so the cost path in AgentRunWriter sees non-zero
        # counts (and tests can assert on them).
        prompt_tokens = sum(max(1, len(msg.content) // 4) for msg in messages)
        completion = f"mock-reply for: {last_user[:64]}"
        return ChatResponse(
            content=completion,
            tool_calls=[],
            finish_reason="stop",
            model=model,
            usage=Usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=max(1, len(completion) // 4),
                total_tokens=prompt_tokens + max(1, len(completion) // 4),
            ),
            response_id="mock-response-id",
        )

    # ------------------------------------------------------------------
    # embed
    # ------------------------------------------------------------------

    async def embed(
        self,
        texts: list[str],
        model: str = "text-embedding-3-small",
    ) -> list[list[float]]:
        """Return a deterministic batch of vectors."""

        if not texts:
            raise LLMProviderError("MockLLMProvider.embed: empty texts list")
        vectors: list[list[float]] = []
        for text in texts:
            if not text:
                raise LLMProviderError("MockLLMProvider.embed: empty text")
            if text in self.embedding_fixtures:
                vec = self.embedding_fixtures[text]
                if len(vec) != self.dim:
                    raise LLMProviderError(
                        f"MockLLMProvider fixture for {text!r} has dim "
                        f"{len(vec)} but provider dim is {self.dim}"
                    )
                vectors.append(list(vec))
            else:
                vectors.append(_seeded_vector(text, model, self.dim))
        return vectors

    # ------------------------------------------------------------------
    # health_check
    # ------------------------------------------------------------------

    async def health_check(self) -> ProviderHealth:
        """Echo back the configured ``health_status`` with sub-ms latency."""

        start = perf_counter()
        # Trivial work so latency_ms is non-zero on a real CPU.
        _ = hashlib.sha256(b"health").hexdigest()
        elapsed_ms = max(0, int((perf_counter() - start) * 1000))
        status = self.health_status
        if status not in ("ok", "degraded", "down"):
            status = "ok"
        return ProviderHealth(
            provider=self.provider_slug,
            status=status,  # type: ignore[arg-type]
            latency_ms=elapsed_ms,
            error_code=None,
            detail=None,
        )


def _seeded_vector(text: str, model: str, dim: int) -> list[float]:
    """Stream ``dim`` floats from a SHA-256 expansion of ``(model, text)``.

    Implementation: SHA-256 produces 32 bytes of entropy per
    round; re-hash ``digest || counter`` until we have ``4 * dim``
    bytes, then unpack as IEEE-754 LE floats shifted into ``[-1, 1]``.

    Not cryptographic — we want cheap, repeatable, distributed-
    looking floats, not unforgeable ones.
    """

    seed = f"{model}::{text}".encode()
    needed_bytes = dim * 4
    chunks: list[bytes] = []
    counter = 0
    produced = 0
    while produced < needed_bytes:
        h = hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
        chunks.append(h)
        produced += len(h)
        counter += 1
    blob = b"".join(chunks)[:needed_bytes]
    raw = struct.unpack(f"<{dim}I", blob)
    return [(value / 0xFFFFFFFF) * 2.0 - 1.0 for value in raw]


__all__ = ["MockLLMProvider"]
