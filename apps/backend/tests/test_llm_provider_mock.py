"""Unit tests for :class:`app.adapters.llm.MockLLMProvider` (PR #17).

Coverage:

* ``embed()`` is deterministic per ``(text, model)`` and respects
  ``dim``.
* ``embed()`` rejects empty text with :class:`LLMProviderError`.
* Fixture-overrides win over the seeded path and validate dim.
* ``complete()`` returns the scripted result when present and a
  deterministic stub otherwise.
* ``dim`` must be positive — defensive guard.
"""

from __future__ import annotations

import pytest

from app.adapters.llm import (
    EmbeddingResult,
    LLMProviderError,
    LLMResult,
    MockLLMProvider,
    Tool,
)


@pytest.mark.asyncio
async def test_embed_is_deterministic_and_dim_matches() -> None:
    provider = MockLLMProvider(dim=8)

    first = await provider.embed("Hello, world", model="text-embedding-3-small")
    second = await provider.embed("Hello, world", model="text-embedding-3-small")

    assert isinstance(first, EmbeddingResult)
    assert first.model == "text-embedding-3-small"
    assert len(first.vector) == 8
    # Vectors are bytes-identical across calls with the same seed.
    assert first.vector == second.vector
    # Range guarantee from ``_seeded_vector``.
    assert all(-1.0 <= component <= 1.0 for component in first.vector)
    # Model is part of the seed → different model ⇒ different vector.
    different_model = await provider.embed("Hello, world", model="other")
    assert different_model.vector != first.vector


@pytest.mark.asyncio
async def test_embed_rejects_empty_text() -> None:
    provider = MockLLMProvider(dim=4)
    with pytest.raises(LLMProviderError) as exc:
        await provider.embed("", model="text-embedding-3-small")
    assert "empty text" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_embed_fixture_overrides_seeded_path() -> None:
    fixture_vec = [0.1, 0.2, 0.3, 0.4]
    provider = MockLLMProvider(
        dim=4,
        embedding_fixtures={"specific text": fixture_vec},
    )
    result = await provider.embed("specific text", model="x")
    assert result.vector == fixture_vec
    # Unrelated text falls through to the seeded path.
    other = await provider.embed("different", model="x")
    assert other.vector != fixture_vec
    assert len(other.vector) == 4


@pytest.mark.asyncio
async def test_embed_fixture_dim_mismatch_raises() -> None:
    provider = MockLLMProvider(
        dim=4,
        embedding_fixtures={"bad": [0.1, 0.2]},  # wrong dim
    )
    with pytest.raises(LLMProviderError) as exc:
        await provider.embed("bad", model="x")
    assert "dim" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_complete_uses_fixture_when_present() -> None:
    scripted = LLMResult(
        text="scripted reply",
        usage={"prompt_tokens": 5, "completion_tokens": 10, "total_tokens": 15},
        model="gpt-4o-mini",
    )
    provider = MockLLMProvider(
        dim=4,
        completion_fixtures={"hello": scripted},
    )
    fixture_result = await provider.complete("hello", model="gpt-4o-mini")
    assert fixture_result is scripted

    # Default stub mentions the prompt prefix and includes positive usage.
    stub = await provider.complete(
        "unscripted prompt",
        model="gpt-4o-mini",
        tools=[Tool(name="search", description="d")],
    )
    assert "unscripted prompt" in stub.text
    assert stub.usage["total_tokens"] > 0


def test_dim_must_be_positive() -> None:
    with pytest.raises(ValueError) as exc:
        MockLLMProvider(dim=0)
    assert "positive" in str(exc.value).lower()
