"""Deterministic mock LLM provider for tests + dev (PR #17).

Two design goals:

1. **Determinism.** Tests must be able to assert on exact byte
   sequences without a fixture-replay rig — re-running
   :meth:`MockLLMProvider.embed` with the same ``text`` always
   returns the same vector. We seed Python's :mod:`hashlib` with the
   input text and stream pseudo-random floats from the digest, so
   the embedding is a pure function of ``(text, model, dim)``.

2. **No real network / no big dependencies.** The mock works on
   stock-Python — no numpy, no torch, no provider SDK. It returns
   plausible-looking vectors so service tests can assert
   "vector length matches DIM" / "norm is finite" without hitting
   the network.

The mock is the default :data:`app.core.config.Settings.llm_provider`
value in dev / CI; production overrides via the env to point at
Polza.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field

from app.adapters.llm.base import (
    EmbeddingResult,
    LLMProvider,
    LLMProviderError,
    LLMResult,
    Tool,
)
from app.models.channel_post_embedding import EMBEDDING_DIM as DEFAULT_DIM


@dataclass
class MockLLMProvider(LLMProvider):
    """Test double that mimics the production provider surface.

    The class is a :func:`~dataclasses.dataclass` so tests can
    construct it inline with ``MockLLMProvider(dim=4, …)`` and
    swap fixtures via the ``embedding_fixtures`` /
    ``completion_fixtures`` dicts when a specific text needs a
    specific reply.
    """

    dim: int = DEFAULT_DIM
    """Embedding dimensionality the mock returns. Matches the
    production default by construction; tests bump it down to a
    small number (e.g. 4) to keep fixtures readable."""

    embedding_fixtures: dict[str, list[float]] = field(default_factory=dict)
    """``text -> vector`` overrides. When a test wants a specific
    vector for a specific input it drops it here and the mock
    short-circuits the hash-derived path."""

    completion_fixtures: dict[str, LLMResult] = field(default_factory=dict)
    """``prompt -> LLMResult`` overrides. Same idea as
    ``embedding_fixtures``; the mock's default reply is an empty
    string + zero usage."""

    def __post_init__(self) -> None:
        if self.dim <= 0:
            raise ValueError(f"MockLLMProvider.dim must be positive, got {self.dim}")

    async def complete(
        self,
        prompt: str,
        model: str,
        *,
        tools: list[Tool] | None = None,
        max_tokens: int = 2000,
    ) -> LLMResult:
        """Return a scripted completion or a deterministic stub.

        ``tools`` is accepted for surface parity but the mock never
        emits tool calls unless the test plants a result in
        ``completion_fixtures``. ``max_tokens`` is ignored — the
        mock has no token budget.
        """

        del tools, max_tokens
        if prompt in self.completion_fixtures:
            return self.completion_fixtures[prompt]
        # Default: deterministic single-word echo so tests that only
        # check "did the provider get called" can assert on the
        # text without precomputing a hash.
        token_estimate = max(1, len(prompt) // 4)
        return LLMResult(
            text=f"mock-reply for: {prompt[:64]}",
            tool_calls=[],
            usage={
                "prompt_tokens": token_estimate,
                "completion_tokens": 4,
                "total_tokens": token_estimate + 4,
            },
            model=model,
        )

    async def embed(self, text: str, model: str) -> EmbeddingResult:
        """Return a deterministic vector derived from ``text``.

        Empty input is rejected: an empty embedding is almost
        always a bug (somebody fed an empty post into the pipeline
        without filtering), and we'd rather surface it as a typed
        error than store a zero vector that silently distorts
        cosine search.
        """

        if not text:
            raise LLMProviderError("MockLLMProvider.embed: empty text")
        if text in self.embedding_fixtures:
            vec = self.embedding_fixtures[text]
            if len(vec) != self.dim:
                raise LLMProviderError(
                    f"MockLLMProvider fixture for {text!r} has dim "
                    f"{len(vec)} but provider dim is {self.dim}"
                )
            return EmbeddingResult(
                vector=list(vec),
                model=model,
                usage={"prompt_tokens": max(1, len(text) // 4)},
            )
        vec = _seeded_vector(text, model, self.dim)
        return EmbeddingResult(
            vector=vec,
            model=model,
            usage={"prompt_tokens": max(1, len(text) // 4)},
        )


def _seeded_vector(text: str, model: str, dim: int) -> list[float]:
    """Stream ``dim`` floats from a SHA-256 expansion of ``(model, text)``.

    Implementation strategy: SHA-256 produces 32 bytes of entropy
    per round; we re-hash ``digest || counter`` until we have ``4 *
    dim`` bytes, then unpack the byte stream as IEEE-754 little-
    endian floats and shift each into the symmetric range
    ``[-1, 1]`` so the resulting vector has the same scale as a
    typical L2-normalised embedding.

    This is intentionally not a cryptographic operation — we want
    cheap, repeatable, distributed-looking floats, not unforgeable
    ones. Using :mod:`hashlib` keeps the implementation portable
    (every Python install ships it) and dependency-free.
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
    # Map uint32 to a symmetric [-1, 1] range. Using a divisor of
    # 2**32 - 1 keeps the floor of the distribution at 0, then we
    # shift to [-1, 1] so cosine similarity behaves sensibly.
    return [(value / 0xFFFFFFFF) * 2.0 - 1.0 for value in raw]


__all__ = [
    "MockLLMProvider",
]
