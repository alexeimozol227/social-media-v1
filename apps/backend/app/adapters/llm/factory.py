"""LLMProvider factory.

PR #20 supersedes the PR #17 ``build_default_provider`` helper.
The factory still picks the concrete provider from
``settings.llm_provider`` but now wires the circuit-breaker
registry + optional response cache for Polza.
"""

from __future__ import annotations

from app.adapters.llm.base import LLMProvider
from app.adapters.llm.circuit_breaker import LLMCircuitBreakerRegistry
from app.adapters.llm.mock import MockLLMProvider
from app.adapters.llm.polza import PolzaProvider, PolzaResponseCache
from app.core.config import settings


def build_default_provider() -> LLMProvider:
    """Construct the provider configured for the current environment.

    Resolution order:

    1. ``settings.llm_provider == "polza"`` and ``polza_api_key`` set
       → :class:`PolzaProvider` (with circuit breaker + optional
       Redis prompt cache).
    2. ``settings.llm_provider == "polza"`` but no API key set →
       hard fail at startup. We deliberately don't fall back to the
       mock — silently degrading to fixtures in production is the
       worst kind of bug.
    3. Everything else → :class:`MockLLMProvider` with the default
       dimensionality from :mod:`app.models.channel_post_embedding`.
    """

    provider_name = settings.llm_provider.lower()
    if provider_name == "polza":
        if not settings.polza_api_key:
            raise RuntimeError(
                "LLM_PROVIDER=polza but POLZA_API_KEY is empty. "
                "Set the API key or switch to LLM_PROVIDER=mock for tests.",
            )
        cache: PolzaResponseCache | None = None
        if settings.llm_prompt_cache_ttl_seconds > 0:
            # Lazy import — production Redis only spins up when the
            # cache is actually enabled, so dev / CI without
            # ``redis`` available still imports cleanly.
            from app.core.redis import get_redis

            cache = PolzaResponseCache(
                redis_client=get_redis(),
                ttl_seconds=settings.llm_prompt_cache_ttl_seconds,
            )

        # Redis-backed breaker registry — same singleton powers
        # every Polza instance in this process.
        try:
            from app.core.redis import get_redis as _get_redis

            registry: LLMCircuitBreakerRegistry | None = LLMCircuitBreakerRegistry(
                redis=_get_redis()
            )
        except Exception:  # pragma: no cover - fall back to in-proc state
            registry = LLMCircuitBreakerRegistry()

        return PolzaProvider(
            api_key=settings.polza_api_key,
            base_url=settings.polza_base_url,
            breaker_registry=registry,
            cache=cache,
        )

    return MockLLMProvider(dim=settings.embedding_dim)


__all__ = ["build_default_provider"]
