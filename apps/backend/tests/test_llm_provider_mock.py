"""Unit tests for :class:`app.adapters.llm.MockLLMProvider` (PR #20).

Coverage:

* ``embed()`` is deterministic per ``(text, model)`` and respects ``dim``.
* ``embed()`` rejects empty input lists / empty texts.
* Fixture-overrides win over the seeded path and validate dim.
* ``chat()`` returns the scripted reply when a fixture matches and
  a deterministic stub otherwise (with non-zero usage).
* ``health_check()`` returns the configured status with a
  non-negative latency.
* ``dim`` must be positive.
"""

from __future__ import annotations

import pytest

from app.adapters.llm import (
    ChatMessage,
    ChatResponse,
    LLMProviderError,
    MockLLMProvider,
    ToolSpec,
    Usage,
)


@pytest.mark.asyncio
async def test_embed_is_deterministic_and_dim_matches() -> None:
    provider = MockLLMProvider(dim=8)

    first = await provider.embed(["Hello, world"], model="text-embedding-3-small")
    second = await provider.embed(["Hello, world"], model="text-embedding-3-small")

    assert len(first) == 1
    assert len(first[0]) == 8
    assert first == second
    assert all(-1.0 <= component <= 1.0 for component in first[0])

    different_model = await provider.embed(["Hello, world"], model="other")
    assert different_model[0] != first[0]


@pytest.mark.asyncio
async def test_embed_supports_batched_inputs() -> None:
    provider = MockLLMProvider(dim=6)
    batch = await provider.embed(["a", "b", "c"], model="text-embedding-3-small")
    assert len(batch) == 3
    assert all(len(vec) == 6 for vec in batch)
    # Each text yields a distinct vector since the seed embeds the text.
    assert batch[0] != batch[1] != batch[2]


@pytest.mark.asyncio
async def test_embed_rejects_empty_inputs() -> None:
    provider = MockLLMProvider(dim=4)
    with pytest.raises(LLMProviderError):
        await provider.embed([], model="text-embedding-3-small")
    with pytest.raises(LLMProviderError):
        await provider.embed([""], model="text-embedding-3-small")


@pytest.mark.asyncio
async def test_embed_fixture_overrides_seeded_path() -> None:
    fixture_vec = [0.1, 0.2, 0.3, 0.4]
    provider = MockLLMProvider(
        dim=4,
        embedding_fixtures={"specific text": fixture_vec},
    )
    result = await provider.embed(["specific text"], model="x")
    assert result[0] == fixture_vec

    other = await provider.embed(["different"], model="x")
    assert other[0] != fixture_vec
    assert len(other[0]) == 4


@pytest.mark.asyncio
async def test_embed_fixture_dim_mismatch_raises() -> None:
    provider = MockLLMProvider(
        dim=4,
        embedding_fixtures={"bad": [0.1, 0.2]},
    )
    with pytest.raises(LLMProviderError) as exc:
        await provider.embed(["bad"], model="x")
    assert "dim" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_chat_uses_fixture_when_present() -> None:
    scripted = ChatResponse(
        content="scripted reply",
        usage=Usage(prompt_tokens=5, completion_tokens=10, total_tokens=15),
        model="gpt-4o-mini",
    )
    provider = MockLLMProvider(
        dim=4,
        chat_fixtures={"hello": scripted},
    )
    messages = [ChatMessage(role="user", content="hello")]
    result = await provider.chat(messages, model="gpt-4o-mini")
    assert result.content == "scripted reply"
    assert result.usage.total_tokens == 15

    stub = await provider.chat(
        [ChatMessage(role="user", content="unscripted prompt")],
        model="gpt-4o-mini",
        tools=[ToolSpec(name="search", description="d")],
    )
    assert "unscripted prompt" in stub.content
    assert stub.usage.total_tokens > 0


@pytest.mark.asyncio
async def test_chat_rejects_empty_messages_list() -> None:
    provider = MockLLMProvider(dim=4)
    with pytest.raises(LLMProviderError):
        await provider.chat([], model="gpt-4o-mini")


@pytest.mark.asyncio
async def test_health_check_returns_configured_status() -> None:
    ok_provider = MockLLMProvider(dim=4, health_status="ok")
    health = await ok_provider.health_check()
    assert health.provider == "mock"
    assert health.status == "ok"
    assert health.latency_ms >= 0

    down_provider = MockLLMProvider(dim=4, health_status="down")
    health_down = await down_provider.health_check()
    assert health_down.status == "down"


def test_dim_must_be_positive() -> None:
    with pytest.raises(ValueError) as exc:
        MockLLMProvider(dim=0)
    assert "positive" in str(exc.value).lower()
