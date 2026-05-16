"""LLM provider adapters (PR #17).

docs/04-architecture.md §16 (LLM gateway abstraction) +
docs/05-tech-stack.md §6: every agent talks to a single
:class:`LLMProvider` Protocol, the concrete implementation is
swapped via :data:`app.core.config.Settings.llm_provider` so tests
run against the deterministic :class:`MockLLMProvider` and
production talks to Polza (https://polza.ai/) without changing
agent code.

PR #17 ships the Protocol + the Mock implementation + a Polza
*skeleton* (everything is wired except the actual ``httpx`` call,
which lands in Sprint 3 alongside the real cost tracking + budget
caps).
"""

from app.adapters.llm.base import (
    EmbeddingResult,
    LLMBudgetExceededError,
    LLMError,
    LLMProvider,
    LLMProviderError,
    LLMResult,
    LLMTimeoutError,
    Tool,
)
from app.adapters.llm.mock import MockLLMProvider
from app.adapters.llm.polza import PolzaProvider, build_default_provider

__all__ = [
    "EmbeddingResult",
    "LLMBudgetExceededError",
    "LLMError",
    "LLMProvider",
    "LLMProviderError",
    "LLMResult",
    "LLMTimeoutError",
    "MockLLMProvider",
    "PolzaProvider",
    "Tool",
    "build_default_provider",
]
