"""Tests for :class:`LLMCircuitBreaker` + registry (PR #20).

docs/plans/phase1-sprint3-plan.md §2.1.4. Covers the state machine
(CLOSED → OPEN → HALF_OPEN → CLOSED) plus Redis-vs-local storage
parity so tests without a real Redis still exercise the contract.
"""

from __future__ import annotations

import asyncio
from typing import cast

import fakeredis.aioredis
import pytest

from app.adapters.llm.circuit_breaker import (
    CircuitBreakerConfig,
    LLMCircuitBreaker,
    LLMCircuitBreakerRegistry,
    _AsyncRedisLike,
)


def _config(fail_threshold: int = 2, reset_seconds: int = 30) -> CircuitBreakerConfig:
    return CircuitBreakerConfig(
        fail_threshold=fail_threshold,
        reset_seconds=reset_seconds,
        namespace="llm:breaker:test",
    )


@pytest.mark.asyncio
async def test_breaker_starts_closed() -> None:
    breaker = LLMCircuitBreaker(
        provider="polza",
        model="gpt-4o-mini",
        config=_config(),
        redis=None,
    )
    assert await breaker.status() == "CLOSED"
    assert await breaker.is_open_async() is False


@pytest.mark.asyncio
async def test_breaker_opens_after_threshold_failures() -> None:
    breaker = LLMCircuitBreaker(
        provider="polza",
        model="gpt-4o-mini",
        config=_config(fail_threshold=2),
        redis=None,
    )
    await breaker.record_failure("LLM_TIMEOUT")
    assert await breaker.status() == "CLOSED"
    await breaker.record_failure("LLM_TIMEOUT")
    assert await breaker.status() == "OPEN"
    assert await breaker.is_open_async() is True


@pytest.mark.asyncio
async def test_breaker_success_resets_failure_counter() -> None:
    breaker = LLMCircuitBreaker(
        provider="polza",
        model="gpt-4o-mini",
        config=_config(fail_threshold=2),
        redis=None,
    )
    await breaker.record_failure("LLM_TIMEOUT")
    await breaker.record_success()
    # Counter cleared — one more failure should *not* open the breaker.
    await breaker.record_failure("LLM_TIMEOUT")
    assert await breaker.status() == "CLOSED"


@pytest.mark.asyncio
async def test_breaker_transitions_to_half_open_after_reset_window() -> None:
    breaker = LLMCircuitBreaker(
        provider="polza",
        model="gpt-4o-mini",
        config=_config(fail_threshold=1, reset_seconds=300),
        redis=None,
    )
    await breaker.record_failure("LLM_TIMEOUT")
    assert await breaker.status() == "OPEN"
    assert await breaker.is_open_async() is True

    # Shrink the reset window so the next ``status()`` call sees it
    # as elapsed without us actually sleeping 5 minutes.
    breaker._config = _config(fail_threshold=1, reset_seconds=0)
    await asyncio.sleep(0)
    assert await breaker.status() == "HALF_OPEN"
    assert await breaker.is_open_async() is False


@pytest.mark.asyncio
async def test_force_open_and_force_close() -> None:
    breaker = LLMCircuitBreaker(
        provider="polza",
        model="gpt-4o-mini",
        config=_config(),
        redis=None,
    )
    await breaker.force_open()
    assert await breaker.is_open_async() is True
    await breaker.force_close()
    assert await breaker.status() == "CLOSED"


@pytest.mark.asyncio
async def test_breaker_persists_state_in_redis() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    redis_typed = cast(_AsyncRedisLike, redis)
    breaker_a = LLMCircuitBreaker(
        provider="polza",
        model="gpt-4o-mini",
        config=_config(fail_threshold=1),
        redis=redis_typed,
    )
    await breaker_a.record_failure("LLM_TIMEOUT")

    breaker_b = LLMCircuitBreaker(
        provider="polza",
        model="gpt-4o-mini",
        config=_config(fail_threshold=1),
        redis=redis_typed,
    )
    assert await breaker_b.status() == "OPEN"


@pytest.mark.asyncio
async def test_registry_caches_breaker_per_provider_model_pair() -> None:
    registry = LLMCircuitBreakerRegistry(config=_config(), redis=None)
    first = await registry.get("polza", "gpt-4o-mini")
    second = await registry.get("polza", "gpt-4o-mini")
    other_model = await registry.get("polza", "gpt-4.1")
    other_provider = await registry.get("mock", "gpt-4o-mini")

    assert first is second
    assert first is not other_model
    assert first is not other_provider
